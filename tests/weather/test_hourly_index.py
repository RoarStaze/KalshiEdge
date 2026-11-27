"""Kalshi Weather Index hourly-only: event time, settlement, and paper safety."""
from __future__ import annotations
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.hourly import (
    CITIES, event_for, parse_index, parse_markets, collect_hourly_once,
    read_hourly_forecasts, journal_hourly_forecasts, settle_hourly_once,
    hourly_calibration,
)

NOW=datetime(2026,11,22,17,20,tzinfo=timezone.utc)
TARGET=datetime(2026,11,22,18,0,tzinfo=timezone.utc)
EVENT="KXTEMPCHIHS-26NOV2213"


def points(now=NOW):
    return {
        "city":"chicago","units":"fahrenheit","config_version":"chicago-temperature-v1.1",
        "timeseries":[
            {"t":int((now-timedelta(minutes=30)).timestamp()*1000),"v":54.1,"status":"normal","contributors":5},
            {"t":int((now-timedelta(minutes=10)).timestamp()*1000),"v":54.7,"status":"normal","contributors":5},
            {"t":int((now-timedelta(minutes=5)).timestamp()*1000),"v":55.0,"status":"normal","contributors":5},
        ],
    }


def market(event=EVENT,status="active",result="",strike=55):
    return {"ticker":event+f"-T{strike-.01:.2f}","event_ticker":event,
            "status":status,"result":result,
            "close_time":(NOW+timedelta(minutes=13)).isoformat(),
            "yes_sub_title":f"{strike}° or above","floor_strike":round(strike-.01,2),
            "rules_primary":"Resolves Yes if the Kalshi Weather Index for Chicago Metro at 1pm ET is at least 55 degrees fahrenheit",
            "settlement_ts":TARGET.isoformat() if status=="settled" else ""}


def transport(request):
    if "/live_data/weather/" in request.url.path:
        return httpx.Response(200,json=points())
    assert request.url.path.endswith("/markets")
    assert request.url.params["series_ticker"]=="KXTEMPCHIHS"
    assert request.url.params["status"]=="open"
    return httpx.Response(200,json={"markets":[market()],"cursor":""})


def test_hourly_index_is_not_daily_airport_high():
    assert CITIES["chicago"].series=="KXTEMPCHIHS"
    assert CITIES["nyc"].series=="KXTEMPNYCHS"
    assert CITIES["la-coastal"].series=="KXTEMPLAXHS"
    assert CITIES["miami"].series=="KXTEMPMIAH"
    assert event_for("chicago",NOW)==(EVENT,TARGET)
    assert event_for("chicago",datetime(2026,11,23,4,20,tzinfo=timezone.utc))[0]=="KXTEMPCHIHS-26NOV2300"


def test_index_rejects_future_unit_backfills_and_unavailable_points():
    good=parse_index(points(), "chicago",NOW)
    assert len(good)==3 and good[-1]["temp_f"]==55.0
    assert good[-1]["event_ms"] < int(NOW.timestamp()*1000)
    with pytest.raises(ValueError):
        parse_index({**points(),"units":"celsius"},"chicago",NOW)
    with pytest.raises(ValueError):
        parse_index({**points(),"city":"nyc"},"chicago",NOW)
    data=points()
    data["timeseries"][0]["receipt_basis"]="backfilled"
    data["timeseries"][1]["status"]="unavailable"
    data["timeseries"][2]["t"]=int((NOW+timedelta(minutes=1)).timestamp()*1000)
    assert parse_index(data,"chicago",NOW)==[]


def test_market_rules_source_and_real_threshold_required():
    assert parse_markets({"markets":[market()],"cursor":""},"chicago",EVENT)[0]["threshold_f"]==55
    for bad in [
        {**market(),"event_ticker":"KXHIGHCHI-26NOV22"},
        {**market(),"rules_primary":"According to The Weather Company at Midway"},
        {**market(),"floor_strike":60},
        {**market(),"status":"closed"},
        {**market(),"yes_sub_title":"55° to 56°"},
    ]:
        assert parse_markets({"markets":[bad],"cursor":""},"chicago",EVENT)==[]



def test_real_hourly_market_floor_strike_is_one_cent_below_displayed_temperature():
    """An observed Kalshi listing displays 55°F or above with floor=54.99."""
    row=market(strike=55)
    assert row["ticker"].endswith("-T54.99")
    accepted=parse_markets({"markets":[row],"cursor":""},"chicago",EVENT)
    assert len(accepted)==1
    assert accepted[0]["threshold_f"]==55
    assert accepted[0]["ticker"]==row["ticker"]
    for wrong_floor in (54.98, 55.01, 60):
        rejected=parse_markets({"markets":[{**row,"floor_strike":wrong_floor}],"cursor":""},
                               "chicago",EVENT)
        assert rejected==[]



def test_hourly_collection_and_prospective_forecast_one_command_compatible(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_hourly_once(db,client=client,cities=("chicago",),clock=lambda:NOW)
    result=asyncio.run(fetch())
    assert result["cities"]["chicago"]["state"]=="READY"
    report=read_hourly_forecasts(tmp_path/"bootstrap",db_path=db,asof=NOW)
    chicago=next(x for x in report["forecasts"] if x["city"]=="chicago")
    assert chicago["status"]=="READY_RESEARCH"
    assert chicago["event_ticker"]==EVENT
    assert chicago["target_utc"]==TARGET.isoformat()
    assert chicago["current_index_f"]==55.0
    assert chicago["model_kind"]=="UNVALIDATED_HOURLY_INDEX_TREND"
    assert chicago["markets"][0]["threshold_f"]==55
    # Actual Kalshi close (17:33 UTC) must not be replaced by target hour (18:00 UTC).
    assert chicago["markets"][0]["close_time_utc"]==(NOW+timedelta(minutes=13)).isoformat()
    assert 0 <= chicago["markets"][0]["probability_yes"]<=1
    assert chicago["source"]=="KALSHI_WEATHER_INDEX"
    assert chicago["real_orders_enabled"] is False
    assert not chicago["calibration_certified"]
    assert journal_hourly_forecasts(db,asof=NOW)["inserted"]==1
    assert journal_hourly_forecasts(db,asof=NOW+timedelta(minutes=4))["inserted"]==0
    with sqlite3.connect(db) as conn:
        row=conn.execute("SELECT event_ticker, asof, source_reading_ms FROM weather_hourly_snapshots").fetchone()
        assert row[0]==EVENT and row[1]==NOW.isoformat()
        assert row[2]<int(NOW.timestamp()*1000)


def test_hourly_series_listing_preserves_real_event_not_constructed_ticker(tmp_path):
    future_event="KXTEMPCHIHS-26NOV2214"
    future_target=datetime(2026,11,22,19,tzinfo=timezone.utc)
    future={**market(event=future_event),
            "close_time":(future_target-timedelta(seconds=30)).isoformat(),
            "series_ticker":"KXTEMPCHIHS"}
    requests_seen=[]
    def response(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=points())
        requests_seen.append(str(request.url))
        assert request.url.params.get("series_ticker")=="KXTEMPCHIHS"
        assert request.url.params.get("status")=="open"
        return httpx.Response(200,json={"markets":[future],"cursor":""})
    db=tmp_path/"weatheredge.sqlite3"
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            return await collect_hourly_once(db,client=client,cities=("chicago",),clock=lambda:NOW)
    status=asyncio.run(collect())["cities"]["chicago"]
    assert status["state"]=="READY"
    assert status["market_strikes"]==1
    assert status["event_ticker"]==future_event
    assert len(requests_seen)==1
    forecast=next(x for x in read_hourly_forecasts(tmp_path,db_path=db,asof=NOW)["forecasts"]
                  if x["city"]=="chicago")
    assert forecast["status"]=="READY_RESEARCH"
    assert forecast["target_utc"]==future_target.isoformat()
    assert forecast["event_ticker"]==future_event
    assert len(forecast["markets"])==1


def test_hourly_listing_uses_series_identity_when_rules_name_synoptic_index():
    row={**market(),"series_ticker":"KXTEMPCHIHS",
         "rules_primary":"The Chicago Metro hourly temperature Index is "
                         "published by the Source Agency using Synoptic Data."}
    parsed=parse_markets({"markets":[row],"cursor":""},"chicago",EVENT)
    assert len(parsed)==1
    assert parsed[0]["ticker"]==row["ticker"]
    assert parse_markets({"markets":[{**row,"series_ticker":"KXTEMPCHIH"}],
                          "cursor":""},"chicago",EVENT)==[]
    assert parse_markets({"markets":[{**row,"rules_primary":"The Weather Company at Midway Airport"}],
                          "cursor":""},"chicago",EVENT)==[]


def test_hourly_series_collector_reports_empty_listings_diagnostically(tmp_path):
    def response(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=points())
        return httpx.Response(200,json={"markets":[],"cursor":""})
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            return await collect_hourly_once(tmp_path/"weatheredge.sqlite3",
                client=client,cities=("chicago",),clock=lambda:NOW)
    data=asyncio.run(collect())["cities"]["chicago"]
    assert data["state"]=="NO_ACTIVE_HOURLY_MARKET"
    assert data["source_market_count"]==0
    assert data["market_strikes"]==0


def test_late_and_unobserved_hour_are_not_predicted(tmp_path):
    report=read_hourly_forecasts(tmp_path/"bootstrap",db_path=tmp_path/"missing.sqlite3",asof=NOW)
    assert all(x["status"]=="MISSING_INDEX" for x in report["forecasts"])
    assert all(not x["markets"] for x in report["forecasts"])
    assert report["real_orders_enabled"] is False


def test_settlement_requires_canonical_hourly_value_and_consistent_yes_result(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    async def setup():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await collect_hourly_once(db,client=client,cities=("chicago",),clock=lambda:NOW)
    asyncio.run(setup())
    journal_hourly_forecasts(db,asof=NOW)
    after=TARGET+timedelta(minutes=12)
    def settled(request):
        if "/live_data/weather/" in request.url.path:
            value=points(after)
            value["timeseries"]=[{"t":int(TARGET.timestamp()*1000),
                                  "v":56.10,"status":"normal","contributors":5}]
            return httpx.Response(200,json=value)
        return httpx.Response(200,json={"markets":[market(status="settled",result="yes")],"cursor":""})
    async def finish():
        async with httpx.AsyncClient(transport=httpx.MockTransport(settled)) as client:
            return await settle_hourly_once(db,client=client,clock=lambda:after)
    out=asyncio.run(finish())
    assert out["verified"]==1
    metrics=hourly_calibration(tmp_path/"bootstrap",db_path=db)
    assert metrics["matched_hours"]==1
    assert metrics["calibration_certified"] is False
    assert metrics["brier"] is None  # no deceptive aggregate with one result
    with sqlite3.connect(db) as conn:
        row=conn.execute("SELECT observed_index_f, result FROM weather_hourly_settlements").fetchone()
        assert row==(56.1,"yes")


def test_settlement_conflict_must_not_be_scored(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    async def setup():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await collect_hourly_once(db,client=client,cities=("chicago",),clock=lambda:NOW)
    asyncio.run(setup())
    journal_hourly_forecasts(db,asof=NOW)
    after=TARGET+timedelta(minutes=12)
    def mismatch(request):
        if "/live_data/weather/" in request.url.path:
            p=points(after)
            p["timeseries"]=[{"t":int(TARGET.timestamp()*1000),
                              "v":50.0,"status":"normal","contributors":5}]
            return httpx.Response(200,json=p)
        return httpx.Response(200,json={"markets":[market(status="settled",result="yes")],"cursor":""})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mismatch)) as client:
            return await settle_hourly_once(db,client=client,clock=lambda:after)
    out=asyncio.run(go())
    assert out["verified"]==0
    assert hourly_calibration(tmp_path/"bootstrap",db_path=db)["matched_hours"]==0



def test_weather_start_runs_hourly_index_not_old_daily_forecast_loops(tmp_path):
    from kalshi_edge.weather.runtime import WeatherSupervisor
    db=tmp_path/"weatheredge.sqlite3"
    async def hourly(path):
        assert path==db
        return {"mode":"HOURLY_PAPER_ONLY","cities":{"chicago":{"state":"READY"}}}
    async def settled(path):
        assert path==db
        return {"mode":"HOURLY_PAPER_ONLY","verified":0,"unverified":0}
    worker=WeatherSupervisor(db,hourly_collect=hourly,
                              hourly_settle=settled)
    async def run():
        await worker.poll_hourly()
        await worker.poll_hourly_settlements()
    asyncio.run(run())
    health=json.loads((db.parent/"collector-status.json").read_text())
    assert health["hourly"]["chicago"]["state"]=="READY"
    assert health["hourly_settlement"]["verified"]==0
    assert health["real_orders_enabled"] is False


def test_weather_ui_only_displays_hourly_index_markets():
    from kalshi_edge.weather.research_ui import render_weather_page
    for section in ("forecast","train","history","data"):
        page=render_weather_page(section)
        assert "KALSHIEDGE / WEATHER · HOURLY INDEX" in page
        if section=="forecast":
            assert "Hourly temperature" in page
            assert "KXTEMPCHIHS" in page
        assert 'data-market="weather"' in page
        assert "PAPER ONLY" in page
        if section=="history":
            assert "/api/weather/hourly/history" in page
            assert "/api/weather/hourly/forecast" not in page
        else:
            assert "/api/weather/hourly/forecast" in page
            assert "/api/weather/hourly/calibration" in page
        assert "Predicted daily maximum" not in page
        assert "Daily high · airport" not in page
        assert "/api/manual-trade/" not in page



def test_hourly_api_routes_are_read_only_in_live_and_preview(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from kalshi_edge.bootstrap.final_history import _make_handler
    from kalshi_edge.bootstrap.preview_site import make_preview_handler
    for preview in (False,True):
        handler=(make_preview_handler() if preview else
           _make_handler(tmp_path/"bootstrap",credential_vault=SimpleNamespace(exists=lambda:True)))
        server=ThreadingHTTPServer(("127.0.0.1",0),handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            with httpx.Client(timeout=5,trust_env=False) as client:
                for route in ("/api/weather/hourly/forecast",
                              "/api/weather/hourly/calibration",
                              "/api/weather/hourly/status"):
                    uri=f"http://127.0.0.1:{server.server_port}"+route
                    response=client.get(uri)
                    assert response.status_code==200
                    assert response.json()["real_orders_enabled"] is False
                    assert client.post(uri,json={}).status_code in (403,404)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)



def test_hourly_market_close_unavailable_never_invented():
    no_close=market()
    no_close.pop("close_time")
    invalid={**market(),"close_time":"2026-11-22T17:33:00"}  # No UTC offset.
    for entry in (no_close,invalid):
        rows=parse_markets({"markets":[entry],"cursor":""},"chicago",EVENT)
        assert len(rows)==1
        assert rows[0]["close_time_utc"] is None


def test_weather_forecast_is_plain_english_and_counts_down_real_close():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    markup=render_hourly_page("forecast", "")
    assert "Predicted temperature" in markup
    assert "Closest Kalshi contract" in markup
    assert "Model leans" in markup
    assert "Market closes in" in markup
    assert 'id="wx-hourly-close-clock"' in markup
    assert 'setInterval(tickMarketClose,1000)' in markup
    assert "close_time_utc" in markup
    assert "Date.now()" in markup
    assert 'id="wx-hourly-minutes"' not in markup
    assert "Minutes to target" not in markup
    assert "Calibration not verified" in markup


def test_hourly_dashboard_javascript_is_syntactically_valid_when_node_available():
    import shutil
    import subprocess
    from kalshi_edge.weather.hourly_ui import render_hourly_page

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    page = render_hourly_page("forecast", "")
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    result = subprocess.run(
        [node, "--check"], input=script, text=True, capture_output=True,
        timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr



def test_hourly_settlement_raw_evidence_tamper_fails_closed(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    async def setup():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await collect_hourly_once(db,client=client,cities=("chicago",),clock=lambda:NOW)
    asyncio.run(setup())
    journal_hourly_forecasts(db,asof=NOW)
    after=TARGET+timedelta(minutes=12)
    def settle_response(request):
        if "/live_data/weather/" in request.url.path:
            p=points(after)
            p["timeseries"]=[{"t":int(TARGET.timestamp()*1000),"v":56.1,
                               "status":"normal","contributors":5}]
            return httpx.Response(200,json=p)
        return httpx.Response(200,json={"markets":[market(status="settled",result="yes")],"cursor":""})
    async def finish():
        async with httpx.AsyncClient(transport=httpx.MockTransport(settle_response)) as client:
            return await settle_hourly_once(db,client=client,clock=lambda:after)
    assert asyncio.run(finish())["verified"]==1
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE weather_hourly_settlements SET source_index_raw='{}'")
    report=hourly_calibration(tmp_path/"bootstrap",db_path=db)
    assert report["matched_hours"]==0
    assert report["status"]=="HOURLY_AUDIT_ERROR"


def test_fall_dst_repeated_eastern_hour_never_duplicates_ambiguous_event():
    # November 1, 2026 at 06:00Z is the *second* 01:00 Eastern; contract
    # methodology chooses the first occurrence of a repeated local hour.
    event,target=event_for("chicago",datetime(2026,11,1,5,40,tzinfo=timezone.utc))
    assert target==datetime(2026,11,1,7,0,tzinfo=timezone.utc)
    assert event=="KXTEMPCHIHS-26NOV0102"


def test_market_parser_accepts_actual_finalized_lifecycle_not_only_settled_filter():
    finalized={**market(status="settled",result="yes"),"status":"finalized",
               "is_provisional":False}
    result=parse_markets({"markets":[finalized],"cursor":""},"chicago",EVENT,settled=True)
    assert len(result)==1 and result[0]["result"]=="yes"
    assert parse_markets({"markets":[finalized],"cursor":""},"chicago",EVENT)==[]
    assert parse_markets({"markets":[{**finalized,"is_provisional":True}],
                          "cursor":""},"chicago",EVENT,settled=True)==[]
