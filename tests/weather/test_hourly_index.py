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
    return {"ticker":event+"-T55","event_ticker":event,
            "status":status,"result":result,
            "yes_sub_title":f"{strike}° or above","floor_strike":strike,
            "rules_primary":"Resolves Yes if the Kalshi Weather Index for Chicago Metro at 1pm ET is at least 55 degrees fahrenheit",
            "settlement_ts":TARGET.isoformat() if status=="settled" else ""}


def transport(request):
    if "/live_data/weather/" in request.url.path:
        return httpx.Response(200,json=points())
    assert request.url.path.endswith("/markets")
    assert request.url.params["event_ticker"]==EVENT
    return httpx.Response(200,json={"markets":[market()],"cursor":""})


def test_hourly_index_is_not_daily_airport_high():
    assert CITIES["chicago"].series=="KXTEMPCHIHS"
    assert CITIES["nyc"].series=="KXTEMPNYCHS"
    assert CITIES["la-coastal"].series=="KXTEMPLAXHS"
    assert CITIES["miami"].series=="KXTEMPMIAH"
    assert event_for("chicago",NOW)==(EVENT,TARGET)
    assert event_for("chicago",datetime(2026,11,23,4,20,tzinfo=timezone.utc))[0]=="KXTEMPCHIHS-26NOV2223"


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
