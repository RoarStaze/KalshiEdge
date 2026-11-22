"""Hourly-only Kalshi Weather Index: test first, no daily proxy labels."""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.hourly import (
    CITIES, parse_index_payload, collect_hourly_indexes,
    read_hourly_forecasts, capture_hourly_forecasts, hourly_health,
    next_settlement_hour,
)


def _utc(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def index_data(city="chicago", when=None, value=61.25, status="normal"):
    t=when or datetime(2026,11,22,17,25,tzinfo=timezone.utc)
    return {"city":city,"units":"fahrenheit",
            "config_version":f"{city}-temperature-v1.0",
            "timeseries":[{"t":int(t.timestamp()*1000),"status":status,
                           "v":value,"contributors":3,"receipt_basis":"canonical"}]}


def test_only_official_index_cities_and_next_utc_hour():
    assert tuple(CITIES)==("chicago","nyc","miami","la-coastal")
    assert all("HS" in CITIES[c]["series"] for c in CITIES)
    assert next_settlement_hour(datetime(2026,11,22,17,25,tzinfo=timezone.utc)) == datetime(
        2026,11,22,18,0,tzinfo=timezone.utc)
    assert next_settlement_hour(datetime(2026,11,22,17,0,tzinfo=timezone.utc)) == datetime(
        2026,11,22,18,0,tzinfo=timezone.utc)


def test_no_unknown_units_city_future_timestamp_or_bad_quorum():
    now=datetime(2026,11,22,17,30,tzinfo=timezone.utc)
    payload=index_data()
    assert parse_index_payload(payload,"chicago",now)[0]["value_f"]==61.25
    for invalid in (
        {**payload,"city":"nyc"},
        {**payload,"units":"celsius"},
        {**payload,"timeseries":[{**payload["timeseries"][0],"contributors":0}]},
        {**payload,"timeseries":[{**payload["timeseries"][0],"v":float("nan")}]},
        index_data(when=now+timedelta(minutes=2)),
    ):
        with pytest.raises(ValueError):
            parse_index_payload(invalid,"chicago",now)


def test_unavailable_index_minute_is_never_fabricated():
    now=datetime(2026,11,22,17,30,tzinfo=timezone.utc)
    payload=index_data(status="unavailable")
    assert parse_index_payload(payload,"chicago",now)==[]


def test_collector_writes_event_time_and_first_observed_receipt(tmp_path):
    when=datetime(2026,11,22,17,25,tzinfo=timezone.utc)
    now=datetime(2026,11,22,17,30,tzinfo=timezone.utc)
    def reply(request):
        assert request.url.host=="external-api.kalshi.com"
        assert request.url.path.endswith("/live_data/weather/chicago")
        assert request.url.params["last_sec"]=="900"
        return httpx.Response(200,json=index_data(when=when))
    async def poll():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            a=await collect_hourly_indexes(tmp_path/"weather.sqlite3",client=client,
                                            cities=("chicago",),clock=lambda:now)
            b=await collect_hourly_indexes(tmp_path/"weather.sqlite3",client=client,
                                            cities=("chicago",),clock=lambda:now+timedelta(minutes=1))
            return a,b
    a,b=asyncio.run(poll())
    assert a["mode"]=="PAPER_ONLY" and a["inserted"]==1
    assert b["inserted"]==0
    with sqlite3.connect(tmp_path/"weather.sqlite3") as con:
        rows=con.execute("SELECT city,event_at,first_received_at,value_f FROM weather_hourly_index").fetchall()
    assert rows==[("chicago",when.isoformat(),now.isoformat(),61.25)]


def test_prediction_is_next_hour_threshold_not_daily_max_and_research_only(tmp_path):
    asof=datetime(2026,11,22,17,30,tzinfo=timezone.utc)
    with sqlite3.connect(tmp_path/"weather.sqlite3") as con:
        con.execute("CREATE TABLE weather_hourly_index(city TEXT,event_at TEXT,first_received_at TEXT,"
                    "value_f REAL,status TEXT,contributors INTEGER,config_version TEXT,"
                    "PRIMARY KEY(city,event_at))")
        con.execute("INSERT INTO weather_hourly_index VALUES(?,?,?,?,?,?,?)",
                    ("chicago",(asof-timedelta(minutes=4)).isoformat(),
                     (asof-timedelta(minutes=2)).isoformat(),61.5,"normal",3,"v1"))
    report=read_hourly_forecasts(tmp_path/"weather.sqlite3",asof=asof)
    item=next(x for x in report["forecasts"] if x["city"]=="chicago")
    assert item["status"]=="READY_RESEARCH"
    assert item["target_hour_utc"]=="2026-11-22T18:00:00+00:00"
    assert item["minutes_remaining"]==30
    assert item["last_index_f"]==61.5
    assert item["model_kind"]=="UNCALIBRATED_INDEX_NOWCAST"
    assert item["tradable"] is False
    assert item["settlement_certified"] is False
    threshold=item["thresholds"]
    assert len(threshold)>3
    assert all(threshold[i]["probability_yes"]>=threshold[i+1]["probability_yes"]
               for i in range(len(threshold)-1))
    assert all(0<=entry["probability_yes"]<=1 for entry in threshold)


def test_no_backfill_no_false_future_and_write_once_forecast(tmp_path):
    db=tmp_path/"data"/"weather"/"weatheredge.sqlite3"
    now=datetime(2026,11,22,17,30,tzinfo=timezone.utc)
    assert capture_hourly_forecasts(db,asof=now)["inserted"]==0
    assert not db.exists()
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE weather_hourly_index(city TEXT,event_at TEXT,first_received_at TEXT,"
                    "value_f REAL,status TEXT,contributors INTEGER,config_version TEXT,"
                    "PRIMARY KEY(city,event_at))")
        con.execute("INSERT INTO weather_hourly_index VALUES(?,?,?,?,?,?,?)",
                    ("chicago",(now-timedelta(minutes=5)).isoformat(),
                     (now-timedelta(minutes=3)).isoformat(),60.0,"normal",3,"v1"))
    first=capture_hourly_forecasts(db,asof=now)
    second=capture_hourly_forecasts(db,asof=now+timedelta(minutes=1))
    assert first["inserted"]==1 and second["inserted"]==0
    with sqlite3.connect(db) as con:
        num=con.execute("SELECT COUNT(*) FROM weather_hourly_forecasts").fetchone()[0]
    assert num==1
    future=read_hourly_forecasts(db,asof=now-timedelta(minutes=10))
    assert next(x for x in future["forecasts"] if x["city"]=="chicago")["status"]!="READY_RESEARCH"


def test_preview_never_uses_local_forecast_file_and_reports_no_fake_accuracy(tmp_path):
    state=read_hourly_forecasts(tmp_path/"does_not_exist",preview=True)
    assert state["mode"]=="PAPER_ONLY"
    assert len(state["forecasts"])==4
    assert all(x["status"]=="PREVIEW_NO_DATA" and not x["thresholds"]
               for x in state["forecasts"])
    health=hourly_health(tmp_path/"does_not_exist",preview=True)
    assert health["hourly_samples"]==0
    assert health["settled_verified"]==0
    assert health["live_orders_enabled"] is False
