"""Market-discovery recovery: source market pages may be stale or incomplete."""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from kalshi_edge.weather.hourly import collect_hourly_once, read_hourly_forecasts

NOW=datetime(2026,11,22,17,20,tzinfo=timezone.utc)
TARGET=datetime(2026,11,22,18,0,tzinfo=timezone.utc)
SERIES="KXTEMPCHIHS"
VALID_EVENT="KXTEMPCHIHS-26NOV2213"
OLD_EVENT="KXTEMPCHIHS-26NOV2210"


def index():
    return {
        "city":"chicago","units":"fahrenheit",
        "config_version":"chicago-temperature-v1.1",
        "timeseries":[
            {"t":int((NOW-timedelta(minutes=6)).timestamp()*1000),
             "v":55.0,"status":"normal","contributors":5},
            {"t":int((NOW-timedelta(minutes=16)).timestamp()*1000),
             "v":54.5,"status":"normal","contributors":5},
        ],
    }


def m(event=VALID_EVENT, *, close=NOW+timedelta(minutes=12), strike=56.):
    return {"ticker":event+f"-T{strike-.01:.2f}",
            "event_ticker":event,"series_ticker":SERIES,
            "market_type":"binary","status":"active",
            "yes_sub_title":f"{strike:.0f}° or above",
            "rules_primary":"Resolves using the Kalshi Weather Index for Chicago Metro",
            "floor_strike":round(strike-.01,2),
            "close_time":close.isoformat()}


def test_fallback_to_open_event_nested_markets_when_market_list_is_stale(tmp_path):
    """Actual event metadata recovers the next listed, strictly verified hour."""
    requests=[]
    def transport(request):
        requests.append((request.url.path,dict(request.url.params)))
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=index())
        if request.url.path.endswith("/markets"):
            return httpx.Response(200,json={"markets":[m(OLD_EVENT)],"cursor":""})
        if request.url.path.endswith("/events"):
            assert request.url.params["series_ticker"]==SERIES
            assert request.url.params["status"]=="open"
            assert request.url.params["with_nested_markets"]=="true"
            return httpx.Response(200,json={"events":[{
                "event_ticker":VALID_EVENT,"series_ticker":SERIES,
                "markets":[m()]}],"cursor":""})
        return httpx.Response(404)
    db=tmp_path/"weather.sqlite3"
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_hourly_once(db,client=client,cities=("chicago",),
                                             clock=lambda:NOW)
    status=asyncio.run(task())["cities"]["chicago"]
    assert status["state"]=="READY"
    assert status["market_strikes"]==1
    assert status["lookup_source"]=="events_fallback"
    assert status["source_market_count"]==1
    assert status["fallback_market_count"]==1
    assert status["event_ticker"]==VALID_EVENT
    forecast=read_hourly_forecasts(tmp_path,db_path=db,asof=NOW)["forecasts"][0]
    assert forecast["status"]=="READY_RESEARCH"
    assert forecast["event_ticker"]==VALID_EVENT
    assert forecast["target_utc"]==TARGET.isoformat()
    assert forecast["markets"][0]["close_time_utc"]==(NOW+timedelta(minutes=12)).isoformat()
    assert any(path.endswith("/events") for path,_ in requests)


def test_no_matching_hour_is_not_reported_as_broken_index_or_fabricated_market(tmp_path):
    """Ten stale exchange markets and zero future events are a market gap."""
    def transport(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=index())
        if request.url.path.endswith("/markets"):
            return httpx.Response(200,json={
                "markets":[m(OLD_EVENT,strike=40+i) for i in range(10)],
                "cursor":""})
        if request.url.path.endswith("/events"):
            return httpx.Response(200,json={"events":[],"cursor":""})
        return httpx.Response(404)
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_hourly_once(tmp_path/"weather.sqlite3",
                client=client,cities=("chicago",),clock=lambda:NOW)
    status=asyncio.run(task())["cities"]["chicago"]
    assert status["state"]=="NO_ACTIVE_HOURLY_MARKET"
    assert status["source_market_count"]==10
    assert status["future_event_count"]==0
    assert status["fallback_state"]=="NO_OPEN_EVENTS"
    assert status["market_strikes"]==0
    assert status["reason_code"]=="NO_FUTURE_INDEX_EVENT"
    assert status["reason"]=="No eligible future hourly Kalshi event is currently listed."
    forecast=read_hourly_forecasts(tmp_path,db_path=tmp_path/"weather.sqlite3",
                                   asof=NOW)["forecasts"][0]
    assert forecast["status"]=="NO_VERIFIED_HOURLY_MARKETS"
    assert forecast["predicted_index_f"] is not None
    assert forecast["markets"]==[]


def test_fallback_does_not_accept_wrong_series_or_closed_contracts(tmp_path):
    """Don't recover another series' airport contract or expired close."""
    def transport(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=index())
        if request.url.path.endswith("/markets"):
            return httpx.Response(200,json={"markets":[m(OLD_EVENT)],"cursor":""})
        if request.url.path.endswith("/events"):
            return httpx.Response(200,json={"events":[
                {"event_ticker":VALID_EVENT,"series_ticker":"KXTEMPCHIH",
                 "markets":[m()]},
                {"event_ticker":VALID_EVENT,"series_ticker":SERIES,
                 "markets":[m(close=NOW-timedelta(seconds=10))]},
            ],"cursor":""})
        return httpx.Response(404)
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_hourly_once(tmp_path/"weather.sqlite3",
                client=client,cities=("chicago",),clock=lambda:NOW)
    status=asyncio.run(task())["cities"]["chicago"]
    assert status["market_strikes"]==0
    assert status["state"]=="NO_ACTIVE_HOURLY_MARKET"


def test_event_fallback_http_failure_preserves_index_and_reports_source_gap(tmp_path):
    def transport(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=index())
        if request.url.path.endswith("/markets"):
            return httpx.Response(200,json={"markets":[m(OLD_EVENT)],"cursor":""})
        if request.url.path.endswith("/events"):
            return httpx.Response(503,json={"message":"temporarily unavailable"})
        return httpx.Response(404)
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_hourly_once(tmp_path/"weather.sqlite3",
                client=client,cities=("chicago",),clock=lambda:NOW)
    status=asyncio.run(task())["cities"]["chicago"]
    assert status["state"]=="NO_ACTIVE_HOURLY_MARKET"
    assert status["fallback_state"]=="UNAVAILABLE"
    assert "503" in status["fallback_error"]
    assert status["market_strikes"]==0


def test_future_but_out_of_horizon_market_is_reported_not_used(tmp_path):
    six_hours=NOW+timedelta(hours=6)
    event="KXTEMPCHIHS-26NOV2219"
    def transport(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=index())
        if request.url.path.endswith("/markets"):
            return httpx.Response(200,json={
                "markets":[m(event,close=six_hours-timedelta(minutes=10))],
                "cursor":""})
        if request.url.path.endswith("/events"):
            return httpx.Response(200,json={"events":[],"cursor":""})
        return httpx.Response(404)
    async def task():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_hourly_once(tmp_path/"weather.sqlite3",
                client=client,cities=("chicago",),clock=lambda:NOW)
    status=asyncio.run(task())["cities"]["chicago"]
    assert status["state"]=="NO_ACTIVE_HOURLY_MARKET"
    assert status["reason_code"]=="LISTED_OUTSIDE_FORECAST_HORIZON"
    assert status["next_listed_target_utc"]==datetime(2026,11,23,0,tzinfo=timezone.utc).isoformat()
    assert status["market_strikes"]==0


def test_no_open_market_has_explicit_no_market_clock_not_unavailable():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("forecast","")
    assert '"NO OPEN MARKET"' in html
    assert '"CLOSE TIME UNKNOWN"' in html
    assert "No eligible future hourly Kalshi event is currently listed." in html
