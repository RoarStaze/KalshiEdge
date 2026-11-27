"""Bitcoin-style Weather History using only frozen, source-audited evidence."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.weather.hourly import _init, event_for
from kalshi_edge.weather.hourly_forward import _init_forward
from kalshi_edge.weather.hourly_history import read_hourly_history

UTC=timezone.utc
START=datetime(2026,11,27,18,tzinfo=UTC)


def _raw(value):
    return json.dumps(value,sort_keys=True,allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record(db,target,*,city="chicago",lead=5,prob=.78,yes="yes",
            model="HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8",legacy=False):
    asof=target-timedelta(minutes=lead)
    event,_=event_for(city,asof)
    ticker=event+"-T54.99"
    close=target-timedelta(minutes=1)
    source_ms=int((asof-timedelta(minutes=2)).timestamp()*1000)
    source={"t":source_ms,"v":54.0,"status":"normal","contributors":5}
    meta={"ticker":ticker,"event_ticker":event,"city":city,
          "source":"KALSHI_WEATHER_INDEX","threshold_f":55.0}
    index={"city":city,"units":"fahrenheit","config_version":city+"-v1",
          "timeseries":[{"t":int(target.timestamp()*1000),
                         "v":56. if yes=="yes" else 54.,
                         "status":"normal","contributors":5}]}
    market={"markets":[{"ticker":ticker,"event_ticker":event,"status":"settled",
                        "result":yes,"yes_sub_title":"55° or above",
                        "floor_strike":54.99,"rules_primary":
                          "Resolves based on Kalshi published Weather Index.",
                        "settlement_ts":(target+timedelta(minutes=6)).isoformat()}],
            "cursor":""}
    checked=(target+timedelta(minutes=12)).isoformat()
    source_json=_raw(source)
    market_json=_raw(market)
    index_json=_raw(index)
    with sqlite3.connect(db) as conn:
        _init(conn)
        _init_forward(conn)
        if legacy:
            snap={"city":city,"event_ticker":event,"target_utc":target.isoformat(),
                  "asof":asof.isoformat(),"model_kind":model,
                  "predicted_index_f":55.5,"sigma_f":1.0,
                  "source_event_ms":source_ms,
                  "source_received_at":(asof-timedelta(minutes=1)).isoformat(),
                  "selected_market":{"ticker":ticker,"threshold_f":55.0,
                                     "probability_yes":prob,
                                     "close_time_utc":close.isoformat()}}
            frozen=_raw(snap)
            conn.execute("""INSERT INTO weather_hourly_snapshots
                (city,event_ticker,market_ticker,asof,target_utc,source_reading_ms,
                 source_receipt,forecast_json,forecast_sha)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
                (city,event,ticker,asof.isoformat(),target.isoformat(),source_ms,
                 snap["source_received_at"],frozen,_sha(frozen)))
            if yes is not None:
                conn.execute("""INSERT INTO weather_hourly_settlements
                (city,event_ticker,market_ticker,result,observed_index_f,
                observed_event_ms,checked_at,source_index_sha,market_sha,
                source_index_raw,market_raw) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (city,event,ticker,yes,56. if yes=="yes" else 54.,
                 int(target.timestamp()*1000),checked,
                 _sha(index_json),_sha(market_json),index_json,market_json))
        else:
            frozen=_raw({
                "city":city,"event_ticker":event,"market_ticker":ticker,
                "target_utc":target.isoformat(),"asof":asof.isoformat(),
                "lead_bucket":lead,"actual_lead_minutes":float(lead),
                "model_kind":model,"predicted_index_f":55.5,"sigma_f":1.0,
                "threshold_f":55.,"probability_yes":prob,
                "market_close_utc":close.isoformat(),
                "market_metadata_received_at":(asof-timedelta(minutes=2)).isoformat(),
                "source_config_version":city+"-v1",
                "source_event_ms":source_ms,
                "source_received_at":(asof-timedelta(minutes=1)).isoformat(),
                "index_source_sha":"a"*64,
                "index_payload":source_json,"index_payload_sha":_sha(source_json),
                "market_metadata":_raw(meta),"market_metadata_sha":_sha(_raw(meta)),
            })
            conn.execute("""INSERT INTO weather_hourly_forward_snapshots
                (city,event_ticker,market_ticker,target_utc,lead_bucket,asof,
                 forecast_json,forecast_sha) VALUES(?,?,?,?,?,?,?,?)""",
                 (city,event,ticker,target.isoformat(),lead,asof.isoformat(),
                  frozen,_sha(frozen)))
            if yes is not None:
                conn.execute("""INSERT OR IGNORE INTO weather_hourly_forward_outcomes
                (city,event_ticker,market_ticker,result,observed_index_f,
                 observed_event_ms,checked_at,index_config_version,
                 source_index_raw,source_index_sha,market_raw,market_sha)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (city,event,ticker,yes,56. if yes=="yes" else 54.,
                 int(target.timestamp()*1000),checked,city+"-v1",
                 index_json,_sha(index_json),market_json,_sha(market_json)))
        conn.commit()
    return event


def test_one_forecast_per_hour_with_latest_v9_checkpoint_and_no_fake_skips(tmp_path):
    db=tmp_path/"weather.sqlite3"
    event=_record(db,START,lead=15,prob=.2,yes="yes")
    _record(db,START,lead=5,prob=.78,yes="yes")
    _record(db,START+timedelta(hours=1),lead=5,prob=.8,yes=None)
    result=read_hourly_history(tmp_path,db_path=db)
    assert result["status"]=="SMALL_SAMPLE_UNCERTIFIED"
    assert result["mode"]=="HOURLY_HISTORY_READ_ONLY"
    assert result["real_orders_enabled"] is False
    assert result["summary"]=={
        "total_final_forecasts":2,"settled_directional_forecasts":1,
        "correct":1,"incorrect":0,"skipped":0,
        "pending_settlement":1,"accuracy":1.0,
    }
    rows=result["forecasts"]
    assert len(rows)==2
    assert rows[0]["verdict"]=="PENDING"
    assert rows[0]["actual_side"] is None
    assert rows[1]["market_ticker"].startswith(event)
    assert rows[1]["forecast"]=="ABOVE"  # Latest 5-min p=.78, not old p=.20.
    assert rows[1]["directional_probability"]==pytest.approx(.78)
    assert rows[1]["confidence"]==pytest.approx(.56)  # Derived strength, not observed accuracy.
    assert rows[1]["market_close"]==(START-timedelta(minutes=1)).isoformat()
    assert rows[1]["verdict"]=="CORRECT"
    assert rows[1]["lead_minutes"]==5


def test_legacy_history_still_appears_without_v9_and_wrong_side_is_incorrect(tmp_path):
    db=tmp_path/"wx.sqlite3"
    _record(db,START,legacy=True,prob=.65,yes="no")
    out=read_hourly_history(tmp_path,db_path=db)
    assert out["summary"]["correct"]==0
    assert out["summary"]["incorrect"]==1
    assert out["summary"]["settled_directional_forecasts"]==1
    assert out["summary"]["accuracy"]==0.0
    assert out["forecasts"][0]["forecast"]=="ABOVE"
    assert out["forecasts"][0]["actual_side"]=="BELOW"
    assert out["forecasts"][0]["verdict"]=="INCORRECT"
    assert out["forecasts"][0]["source"]=="LEGACY_PRECOMMITTED"


def test_ten_unique_verified_events_unlock_accuracy_without_double_count(tmp_path):
    db=tmp_path/"wx.sqlite3"
    for i in range(10):
        target=START+timedelta(hours=i)
        _record(db,target,lead=15,prob=.7,yes=None)  # No outcome until final 5-minute fixture.
        _record(db,target,lead=5,prob=.9,yes="yes" if i<8 else "no")
    out=read_hourly_history(tmp_path,db_path=db)
    assert out["summary"]["total_final_forecasts"]==10
    assert out["summary"]["correct"]==8
    assert out["summary"]["incorrect"]==2
    assert out["summary"]["accuracy"]==pytest.approx(.8)
    assert all(r["lead_minutes"]==5 for r in out["forecasts"])


def test_tampered_source_or_hash_fails_closed_instead_of_false_accuracy(tmp_path):
    db=tmp_path/"wx.sqlite3"
    _record(db,START,lead=5)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE weather_hourly_forward_outcomes SET market_raw='{}'")
    out=read_hourly_history(tmp_path,db_path=db)
    assert out["status"]=="HOURLY_HISTORY_AUDIT_ERROR"
    assert out["summary"]["correct"]==0
    assert out["summary"]["accuracy"] is None
    assert out["forecasts"]==[]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE weather_hourly_forward_outcomes SET market_raw=market_raw")
        conn.execute("UPDATE weather_hourly_forward_snapshots SET forecast_sha='faked'")
    assert read_hourly_history(tmp_path,db_path=db)["status"]=="HOURLY_HISTORY_AUDIT_ERROR"


def test_missing_database_and_preview_are_empty_not_synthetic(tmp_path):
    empty=read_hourly_history(tmp_path,preview=True)
    assert empty["summary"]["total_final_forecasts"]==0
    assert empty["summary"]["accuracy"] is None
    assert empty["forecasts"]==[]
    assert empty["real_orders_enabled"] is False
    assert read_hourly_history(tmp_path)["forecasts"]==[]


def test_history_page_matches_bitcoin_cards_table_and_filter():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("history","")
    for x in ('Settled forecast accuracy','Correct / Incorrect','SKIP decisions',
              'Pending settlement','Recorded hourly forecasts',
              'Market closes (local)','Market','Forecast','Probability',
              'Confidence','Settled outcome','Result',
              'id="wx-history-filter"','id="wx-history-rows"',
              'id="wx-history-accuracy"','id="wx-history-correctness"',
              'id="wx-history-skips"','id="wx-history-pending"',
              '/api/weather/hourly/history','No matching forecasts',
              'value="CORRECT"','value="INCORRECT"','value="PENDING"'):
        assert x in html
    assert "Confidence is derived" in html
    assert "small sample" in html.lower()
    assert "SKIP decisions are not recorded" in html
    assert "<table" in html
    assert 'class="wx-trade-grid"' not in html
    assert 'id="wx-hm-matched"' not in html # Accuracy detail moved to Train.
    assert "/api/manual-trade/" not in html


def test_history_frontend_js_parses_with_node_if_available():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    node=shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    page=render_hourly_page("history","")
    for fragment in page.split("<script>")[1:]:
        script=fragment.split("</script>",1)[0]
        result=subprocess.run([node,"--check"],input=script,capture_output=True,
                              text=True,timeout=12)
        assert result.returncode==0,result.stderr


def test_history_endpoint_live_and_preview_remain_read_only(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from types import SimpleNamespace
    from kalshi_edge.bootstrap.preview_site import make_preview_handler
    from kalshi_edge.bootstrap.final_history import _make_handler
    import httpx

    for preview in (False,True):
        handler=make_preview_handler() if preview else _make_handler(
            tmp_path/"bootstrap",credential_vault=SimpleNamespace(exists=lambda:True))
        server=ThreadingHTTPServer(("127.0.0.1",0),handler)
        worker=Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            with httpx.Client(timeout=8,trust_env=False) as client:
                url=f"http://127.0.0.1:{server.server_port}/api/weather/hourly/history"
                reply=client.get(url)
                assert reply.status_code==200
                data=reply.json()
                assert data["mode"]=="HOURLY_HISTORY_READ_ONLY"
                assert data["summary"]["accuracy"] is None
                assert data["real_orders_enabled"] is False
                assert client.post(url,json={"buy":True}).status_code in (403,404,405)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)


def test_directional_strength_and_accuracy_after_one_verification(tmp_path):
    db=tmp_path/"verified.sqlite3"
    _record(db,START,lead=5,prob=.82,yes="yes")
    first=read_hourly_history(tmp_path,db_path=db)
    assert first["summary"]["correct"]==1
    assert first["summary"]["incorrect"]==0
    assert first["summary"]["accuracy"]==1.0
    assert first["status"]=="SMALL_SAMPLE_UNCERTIFIED"
    assert first["forecasts"][0]["confidence"]==pytest.approx(.64)
    # Reload from the same on-disk evidence: no hindsight recomputation of forecasts.
    again=read_hourly_history(tmp_path,db_path=db)
    assert again["summary"]["accuracy"]==1.0
    assert again["forecasts"][0]["verdict"]=="CORRECT"

def test_small_sample_one_right_one_wrong_reports_real_ratio(tmp_path):
    db=tmp_path/"mixed.sqlite3"
    _record(db,START,lead=5,prob=.76,yes="yes")
    _record(db,START+timedelta(hours=1),lead=5,prob=.66,yes="no")
    report=read_hourly_history(tmp_path,db_path=db)
    assert (report["summary"]["correct"],report["summary"]["incorrect"])==(1,1)
    assert report["summary"]["accuracy"]==.5
    assert report["status"]=="SMALL_SAMPLE_UNCERTIFIED"
    assert report["calibration_certified"] is False


def test_finalized_contract_result_unlocks_correct_and_incorrect_without_index(tmp_path):
    """The real Kalshi market is authoritative when canonical index publication lags."""
    import asyncio
    import httpx
    from kalshi_edge.weather.hourly_market_results import collect_final_market_results_once
    db=tmp_path/"wx.sqlite3"
    _record(db,START,lead=5,prob=.82,yes=None)
    _record(db,START+timedelta(hours=1),lead=5,prob=.7,yes=None)
    def transport(request):
        assert request.url.path.startswith("/trade-api/v2/markets/")
        ticker=request.url.path.rsplit("/",1)[-1]
        hour=18 if "2713-" in ticker else 19
        target=START+timedelta(hours=hour-18)
        side="yes" if hour==18 else "no"
        return httpx.Response(200,json={"market":{
            "ticker":ticker,"event_ticker":ticker.rsplit("-T",1)[0],
            "status":"finalized","is_provisional":False,"result":side,
            "yes_sub_title":"55° or above","floor_strike":54.99,
            "rules_primary":"Resolves based on Kalshi published Weather Index.",
            "settlement_ts":(target+timedelta(minutes=8)).isoformat()}})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_final_market_results_once(
                db,client=client,clock=lambda:START+timedelta(hours=1,minutes=15))
    collected=asyncio.run(go())
    assert collected["verified"]==2,collected
    report=read_hourly_history(tmp_path,db_path=db)
    assert report["summary"]["correct"]==1
    assert report["summary"]["incorrect"]==1
    assert report["summary"]["accuracy"]==.5
    assert report["summary"]["pending_settlement"]==0
    assert sorted(r["verdict"] for r in report["forecasts"])==["CORRECT","INCORRECT"]
    assert all(r["evidence_kind"]=="KALSHI_FINALIZED_MARKET" for r in report["forecasts"])
    # Persisted, not recomputed after restart.
    assert read_hourly_history(tmp_path,db_path=db)["summary"]["accuracy"]==.5


def test_market_result_rejects_unfinalized_provisional_and_wrong_ticker(tmp_path):
    import asyncio
    import httpx
    from kalshi_edge.weather.hourly_market_results import collect_final_market_results_once
    db=tmp_path/"wx.sqlite3"
    _record(db,START,lead=5,prob=.82,yes=None)
    for state,provisional,wrong_ticker in [
        ("closed",False,False),("determined",False,False),
        ("finalized",True,False),("finalized",False,True)]:
        def transport(request):
            ticker=request.url.path.rsplit("/",1)[-1]
            return httpx.Response(200,json={"market":{
                "ticker":"OTHER" if wrong_ticker else ticker,
                "event_ticker":ticker.rsplit("-T",1)[0],
                "status":state,"is_provisional":provisional,"result":"yes",
                "yes_sub_title":"55° or above","floor_strike":54.99,
                "rules_primary":"Kalshi Weather Index.",
                "settlement_ts":(START+timedelta(minutes=8)).isoformat()}})
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                return await collect_final_market_results_once(
                    db,client=client,clock=lambda:START+timedelta(minutes=15))
        assert asyncio.run(go())["verified"]==0
        report=read_hourly_history(tmp_path,db_path=db)
        assert report["summary"]["pending_settlement"]==1
        assert report["summary"]["correct"]==0
        assert report["summary"]["accuracy"] is None


def test_market_only_result_tamper_fails_closed(tmp_path):
    import asyncio
    import httpx
    from kalshi_edge.weather.hourly_market_results import collect_final_market_results_once
    db=tmp_path/"wx.sqlite3"
    _record(db,START,lead=5,prob=.82,yes=None)
    def transport(request):
        ticker=request.url.path.rsplit("/",1)[-1]
        return httpx.Response(200,json={"market":{
            "ticker":ticker,"event_ticker":ticker.rsplit("-T",1)[0],
            "status":"finalized","result":"yes","is_provisional":False,
            "yes_sub_title":"55° or above","floor_strike":54.99,
            "rules_primary":"Kalshi Weather Index.",
            "settlement_ts":(START+timedelta(minutes=8)).isoformat()}})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_final_market_results_once(
                db,client=client,clock=lambda:START+timedelta(minutes=15))
    assert asyncio.run(go())["verified"]==1
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE weather_hourly_final_market_results SET market_raw='{}'")
    report=read_hourly_history(tmp_path,db_path=db)
    assert report["status"]=="HOURLY_HISTORY_AUDIT_ERROR"
    assert report["summary"]["correct"]==0
    assert report["forecasts"]==[]
