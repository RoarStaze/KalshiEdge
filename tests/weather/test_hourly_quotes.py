"""Source-verified hourly Kalshi ask/size evidence; no orders or invented odds."""
from __future__ import annotations
import asyncio
import json
import sqlite3
from datetime import datetime,timedelta,timezone

import httpx
import pytest

from kalshi_edge.weather.hourly_quotes import (
    parse_live_market_quote, _init_quotes, store_quote,
    read_latest_quote, collect_hourly_quotes_once
)
from kalshi_edge.weather.hourly import _init as init_hourly,read_hourly_forecasts

NOW=datetime(2026,11,22,17,20,tzinfo=timezone.utc)
TARGET=datetime(2026,11,22,18,0,tzinfo=timezone.utc)
EVENT="KXTEMPCHIHS-26NOV2213"
TICKER=EVENT+"-T54.99"

def market(*,status="active",close=None,ticker=TICKER):
    return {
      "ticker":ticker,"event_ticker":EVENT,"series_ticker":"KXTEMPCHIHS",
      "market_type":"binary","status":status,
      "rules_primary":"Kalshi Weather Index for Chicago Metro",
      "yes_sub_title":"55° or above","floor_strike":54.99,
      "close_time":(close or (NOW+timedelta(minutes=12))).isoformat(),
      "yes_ask_dollars":"0.5600","yes_ask_size_fp":"10.00",
      "no_ask_dollars":"0.4500","no_ask_size_fp":"8.00",
      "last_price_dollars":"0.4800",
    }

def index():
    return {"city":"chicago","units":"fahrenheit",
      "config_version":"chicago-temperature-v1.1",
      "timeseries":[
        {"t":int((NOW-timedelta(minutes=18)).timestamp()*1000),
         "v":54.9,"status":"normal","contributors":5},
        {"t":int((NOW-timedelta(minutes=5)).timestamp()*1000),
         "v":55.0,"status":"normal","contributors":5}
      ]}

def fixture_db(conn):
    init_hourly(conn)
    raw={"t":int((NOW-timedelta(minutes=5)).timestamp()*1000),
         "v":55.0,"status":"normal","contributors":5}
    conn.execute("""INSERT INTO weather_hourly_index
      (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
      VALUES(?,?,?,?,?,?,?,?)""",("chicago",raw["t"],
        (NOW-timedelta(minutes=4)).isoformat(),55.,"normal",
        "chicago-temperature-v1.1","a"*64,json.dumps(raw)))
    original={"source":"KALSHI_WEATHER_INDEX",
              "close_time_utc":(NOW+timedelta(minutes=12)).isoformat()}
    conn.execute("""INSERT INTO weather_hourly_markets
      (ticker,city,event_ticker,target_utc,threshold_f,received_at,metadata)
      VALUES(?,?,?,?,?,?,?)""",(TICKER,"chicago",EVENT,
      TARGET.isoformat(),55.0,NOW.isoformat(),json.dumps(original)))
    conn.commit()

def parse(raw=None):
    return parse_live_market_quote(raw or market(),city="chicago",
             event=EVENT,ticker=TICKER,threshold_f=55.0,receipt=NOW)

def test_parse_exact_market_asks_and_sizes_independent_of_model_confidence():
    evidence=parse()
    assert evidence["status"]=="VALID_ASK"
    assert evidence["yes_ask_dollars"]=="0.5600"
    assert evidence["no_ask_dollars"]=="0.4500"
    assert evidence["yes_ask_size_fp"]=="10.00"
    assert evidence["no_ask_size_fp"]=="8.00"
    assert evidence["close_time_utc"]==(NOW+timedelta(minutes=12)).isoformat()
    assert "model_probability_yes" not in evidence
    assert "expected_profit" not in evidence

@pytest.mark.parametrize("change",[
    {"ticker":"KXTEMPCHIHS-WRONG-T54.99"},
    {"series_ticker":"KXTEMPCHIH"},
    {"event_ticker":"KXTEMPCHIHS-26NOV2214"},
    {"floor_strike":59.99},
    {"rules_primary":"The Weather Company at Midway"},
    {"yes_sub_title":"56° or above"}
])
def test_quote_identity_or_settlement_mismatch_rejected(change):
    with pytest.raises(ValueError):
        parse({**market(),**change})

@pytest.mark.parametrize("change",[
    {"yes_ask_dollars":"-0.01"},{"no_ask_dollars":"NaN"},
    {"yes_ask_dollars":"1.2"},{"yes_ask_size_fp":"-2.0"},
    {"no_ask_size_fp":"Infinity"}
])
def test_malformed_price_or_size_fails_closed(change):
    with pytest.raises(ValueError):
        parse({**market(),**change})

def test_quote_without_positive_ask_size_is_not_called_buyable():
    row={**market(),"yes_ask_dollars":"0.0000","yes_ask_size_fp":"0.00",
         "no_ask_dollars":"0.4500","no_ask_size_fp":"0.00"}
    result=parse(row)
    assert result["status"]=="NO_VISIBLE_ASK"
    assert result["yes_ask_dollars"] is None
    assert result["no_ask_dollars"] is None

def test_closed_contract_never_exposes_open_ask():
    closed=parse({**market(),"status":"closed"})
    assert closed["status"]=="CLOSED"
    assert closed["yes_ask_dollars"] is None
    expired=parse(market(close=NOW-timedelta(seconds=5)))
    assert expired["status"]=="CLOSED"
    assert expired["no_ask_dollars"] is None

def test_quote_cache_requires_exact_identity_integrity_and_fresh_receipt():
    conn=sqlite3.connect(":memory:")
    init_hourly(conn);_init_quotes(conn)
    evidence=parse()
    store_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                threshold_f=55.0,receipt=NOW,quote=evidence)
    fresh=read_latest_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                            threshold_f=55.,asof=NOW+timedelta(seconds=30))
    assert fresh["status"]=="VALID_ASK"
    assert fresh["yes_ask_dollars"]=="0.5600"
    assert read_latest_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                             threshold_f=55.,asof=NOW+timedelta(seconds=36))["status"]=="STALE_QUOTE"
    assert read_latest_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                             threshold_f=56.,asof=NOW)["status"]=="QUOTE_UNAVAILABLE"
    assert read_latest_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                             threshold_f=55.,asof=NOW-timedelta(minutes=1))["status"]=="QUOTE_PENDING"
    conn.execute("UPDATE weather_hourly_quote_snapshots SET payload_sha256='0'")
    assert read_latest_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                             threshold_f=55.,asof=NOW)["status"]=="CORRUPTED_QUOTE"

def test_hourly_quote_collector_checks_only_actual_listed_ticker_and_never_orders(tmp_path):
    calls=[]
    db=tmp_path/"weather.sqlite3"
    conn=sqlite3.connect(db);fixture_db(conn);conn.close()
    def respond(request):
        calls.append(str(request.url))
        if request.url.path=="/trade-api/v2/markets/"+TICKER:
            return httpx.Response(200,json={"market":market()})
        return httpx.Response(404)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await collect_hourly_quotes_once(db,client=client,clock=lambda:NOW)
    status=asyncio.run(run())
    assert status["mode"]=="HOURLY_QUOTES_READ_ONLY"
    assert status["real_orders_enabled"] is False
    assert status["checked"]==1
    assert status["valid_asks"]==1
    assert len(calls)==1
    assert calls[0].endswith("/markets/"+TICKER)
    forecast=next(x for x in read_hourly_forecasts(tmp_path,db_path=db,asof=NOW)["forecasts"] if x["city"]=="chicago")
    assert forecast["markets"][0]["quote_status"]=="VALID_ASK"
    assert forecast["markets"][0]["yes_ask_dollars"]=="0.5600"
    assert forecast["real_orders_enabled"] is False

def test_quote_endpoint_failure_does_not_break_weather_index_forecast(tmp_path):
    db=tmp_path/"weather.sqlite3"
    conn=sqlite3.connect(db);fixture_db(conn);conn.close()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
             lambda req:httpx.Response(503))) as client:
            return await collect_hourly_quotes_once(db,client=client,clock=lambda:NOW)
    status=asyncio.run(run())
    assert status["unavailable"]==1
    forecast=read_hourly_forecasts(tmp_path,db_path=db,asof=NOW)["forecasts"][0]
    assert forecast["current_index_f"]==55
    assert forecast["markets"][0]["quote_status"] in ("QUOTE_PENDING","QUOTE_UNAVAILABLE")
    assert forecast["real_orders_enabled"] is False

def test_verified_closed_quote_removes_contract_from_research_probability_ladder(tmp_path):
    db=tmp_path/"weather.sqlite3"
    with sqlite3.connect(db) as conn:
        fixture_db(conn);_init_quotes(conn)
        closed=parse({**market(),"status":"closed"})
        store_quote(conn,city="chicago",event=EVENT,ticker=TICKER,
                    threshold_f=55.0,receipt=NOW,quote=closed)
        conn.commit()
    forecast=read_hourly_forecasts(tmp_path,db_path=db,asof=NOW)["forecasts"][0]
    assert forecast["markets"]==[]
    assert forecast["status"]=="NO_VERIFIED_HOURLY_MARKETS"

def test_weather_page_distinguishes_model_odds_from_kalshi_ask_and_quote_age():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("forecast","")
    assert "Kalshi ask YES / NO" in html
    assert "Observed quote status" in html
    assert "quote_status" in html
    assert "yes_ask_dollars" in html
    assert "no_ask_dollars" in html
    assert "not guaranteed executable" in html

def test_supervisor_automatically_refreshes_quotes_in_separate_loop(tmp_path):
    from kalshi_edge.weather.runtime import WeatherSupervisor,HOURLY_QUOTES_SECONDS
    assert HOURLY_QUOTES_SECONDS==20
    async def collect(path):
        assert path==tmp_path/"weather.sqlite3"
        return {"mode":"HOURLY_QUOTES_READ_ONLY","real_orders_enabled":False,
                "checked":1,"valid_asks":1,"no_visible_ask":0,"unavailable":0}
    worker=WeatherSupervisor(tmp_path/"weather.sqlite3",hourly_quotes_collect=collect)
    asyncio.run(worker.poll_hourly_quotes())
    state=json.loads((tmp_path/"collector-status.json").read_text())
    assert state["hourly_quotes"]["state"]=="READY"
    assert state["hourly_quotes"]["valid_asks"]==1
    assert state["real_orders_enabled"] is False


def test_feed_status_discloses_separate_quote_health_and_unavailable_reasons():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("forecast","")
    assert "Kalshi quote feed" in html
    assert "hourly_quotes" in html
    assert "NO_VISIBLE_ASK" in html
