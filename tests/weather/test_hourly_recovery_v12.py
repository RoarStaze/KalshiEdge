"""Weather V12: recover old official results and validate a genuinely prospective model challenger."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.hourly import _init, event_for
from kalshi_edge.weather.hourly_history import read_hourly_history
from kalshi_edge.weather.hourly_market_results import collect_final_market_results_once
from kalshi_edge.weather.hourly_nowcast import _case, choose_index_nowcast

UTC=timezone.utc
NOW=datetime(2026,11,27,23,30,tzinfo=UTC)


def freeze(db,target,city="chicago",p=.8):
    from kalshi_edge.weather.hourly import _init
    asof=target-timedelta(minutes=20)
    event,_=event_for(city,asof)
    ticker=event+"-T54.99"
    source_ms=int((asof-timedelta(minutes=2)).timestamp()*1000)
    raw=json.dumps({
        "city":city,"event_ticker":event,"asof":asof.isoformat(),
        "target_utc":target.isoformat(),"model_kind":"V12_TEST",
        "source_event_ms":source_ms,
        "source_received_at":(asof-timedelta(minutes=1)).isoformat(),
        "selected_market":{"ticker":ticker,"threshold_f":55.,
           "probability_yes":p,"close_time_utc":(target-timedelta(minutes=1)).isoformat()}
    },sort_keys=True)
    with sqlite3.connect(db) as c:
        _init(c)
        c.execute("""INSERT OR IGNORE INTO weather_hourly_snapshots(
            city,event_ticker,market_ticker,asof,target_utc,source_reading_ms,
            source_receipt,forecast_json,forecast_sha)
            VALUES(?,?,?,?,?,?,?,?,?)""",(
            city,event,ticker,asof.isoformat(),target.isoformat(),source_ms,
            (asof-timedelta(minutes=1)).isoformat(),raw,
            hashlib.sha256(raw.encode()).hexdigest()))
    return event,ticker,target


def finalized(record,side="yes"):
    event,ticker,target=record
    return {"market":{
        "ticker":ticker,"event_ticker":event,
        "status":"finalized","is_provisional":False,"result":side,
        "yes_sub_title":"55° or above","floor_strike":54.99,
        "rules_primary":"Resolves based on Kalshi Weather Index",
        "settlement_ts":(target+timedelta(minutes=8)).isoformat()}}


def collect(db,transport,clock=NOW):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_final_market_results_once(
                db,client=client,clock=lambda:clock)
    return asyncio.run(go())


def test_recover_14_day_old_finalized_history_without_truncating_it(tmp_path):
    db=tmp_path/"wx.sqlite3"
    record=freeze(db,NOW.replace(minute=0)-timedelta(days=14))
    result=collect(db,lambda req:httpx.Response(200,json=finalized(record)))
    assert result["verified"]==1,result
    report=read_hourly_history(tmp_path,db_path=db)
    assert report["summary"]["correct"]==1
    assert report["summary"]["pending_settlement"]==0
    assert report["forecasts"][0]["evidence_kind"]=="KALSHI_FINALIZED_MARKET"


def test_recovery_batches_are_bounded_and_visit_both_old_and_new(tmp_path):
    db=tmp_path/"wx.sqlite3"
    base=NOW.replace(minute=0)
    rows=[freeze(db,base-timedelta(hours=i+1)) for i in range(72)]
    calls=[]
    data={r[1]:r for r in rows}
    def response(req):
        ticker=req.url.path.rsplit("/",1)[-1]
        calls.append(ticker)
        return httpx.Response(200,json=finalized(data[ticker]))
    result=collect(db,response)
    assert 0<result["checked"]<=32,result
    assert result["verified"]==result["checked"]
    assert len(calls)==result["checked"]
    assert rows[0][1] in calls
    assert rows[-1][1] in calls
    assert result["remaining"]>0
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM weather_hourly_final_market_results").fetchone()[0]==result["verified"]


def test_unfinalized_market_failure_reason_is_stored_and_exposed(tmp_path):
    db=tmp_path/"wx.sqlite3"
    record=freeze(db,NOW.replace(minute=0)-timedelta(hours=5))
    def failing(req):
        return httpx.Response(503,json={"error":"unavailable"})
    result=collect(db,failing)
    assert result["verified"]==0
    assert result["unverified"]>=1
    with sqlite3.connect(db) as c:
        status,detail=c.execute("""SELECT state,detail
            FROM weather_hourly_final_market_checks WHERE market_ticker=?""",
            (record[1],)).fetchone()
    assert status=="FETCH_ERROR"
    assert "503" in detail
    history=read_hourly_history(tmp_path,db_path=db)
    assert history["summary"]["accuracy"] is None
    row=history["forecasts"][0]
    assert row["verdict"]=="PENDING"
    assert row["settlement_check_state"]=="FETCH_ERROR"
    assert row["settlement_check_reason"] is not None


def test_analogue_never_uses_latest_receipt_or_post_anchor_data():
    target=datetime(2026,11,25,19,tzinfo=UTC)
    anchor=target-timedelta(minutes=30)
    points=[]
    for day in range(3):
        base=datetime(2026,11,23+day,18,0,tzinfo=UTC)
        for i in range(13):
            t=base+timedelta(minutes=i*5)
            v=50.+(i/12)*4 if day==0 else 55.+(i/12)*3 if day==1 else 58.+(i/12)*5
            received=t+timedelta(minutes=1)
            points.append((int(t.timestamp()*1000),int(received.timestamp()*1000),v,"normal"))
    # Current hour available, prior-day analogue must be drawn only from
    # yesterday's 18:30 and 19:00, never from today's 19:00 target.
    points.append((int(target.timestamp()*1000),
                   int((target+timedelta(minutes=1)).timestamp()*1000),66.,"normal"))
    points.sort()
    row=_case(points,[p[0] for p in points],int(target.timestamp()*1000),30)
    assert row is not None
    assert row.get("diurnal_delta_f") is not None
    mutated=[p for p in points if p[0]!=int(target.timestamp()*1000)]
    mutated.append((int(target.timestamp()*1000),
                    int(target.timestamp()*1000),999.,"normal"))
    mutated.sort()
    copy=_case(mutated,[p[0] for p in mutated],int(target.timestamp()*1000),30)
    assert copy is not None
    assert copy["diurnal_delta_f"]==row["diurnal_delta_f"]


def test_diurnal_candidate_only_promotes_with_genuine_chronological_gain():
    cases=[]
    t=datetime(2026,7,1,tzinfo=UTC)
    for i in range(180):
        analog=(1 if i%2 else -1)*1.5
        cases.append({
            "target_utc":(t+timedelta(hours=i)).isoformat(),
            "current_index_f":50.,"fast_slope":0.,"slow_slope":0.,
            "hours_ahead":.5,"baseline_mean_f":50.,"baseline_sigma_f":1.5,
            "diurnal_delta_f":analog,"observed_index_f":50.+analog,
        })
    choice=choose_index_nowcast(cases)
    assert choice["enabled"],choice
    assert choice["model"]=="DIURNAL_ANALOGUE_V5"
    assert choice["validation"]["candidate_mae_f"]<choice["validation"]["baseline_mae_f"]
    without=[{**row,"diurnal_delta_f":0.} for row in cases]
    no_gain=choose_index_nowcast(without)
    assert no_gain["model"]!="DIURNAL_ANALOGUE_V5" or not no_gain["enabled"]


def test_history_reports_collector_heartbeat_instead_of_only_page_refresh(tmp_path):
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    db=tmp_path/"wx.sqlite3"
    freeze(db,NOW.replace(minute=0)-timedelta(hours=2))
    history=read_hourly_history(tmp_path,db_path=db)
    assert history["collector"]["state"]=="NOT_STARTED"
    html=render_hourly_page("history","")
    assert 'id="wx-history-collector-warning"' in html
    assert 'data.collector' in html
    assert "COLLECTOR" in html
