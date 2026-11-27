"""V9 prospective hourly source-reconciled forecast evidence; synthetic fixtures only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.hourly import _init, event_for
from kalshi_edge.weather.hourly_forward import (
    forward_accuracy, journal_forward, settle_forward_once,
)

UTC=timezone.utc
NOW=datetime(2026,11,27,17,30,tzinfo=UTC)


def sample(city="chicago",asof=NOW,lead=30,model="HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8"):
    target=asof+timedelta(minutes=lead)
    assert target.minute==0
    event,_=event_for(city,asof)
    observed=asof-timedelta(minutes=2)
    return {
        "city":city,"event_ticker":event,"target_utc":target.isoformat(),
        "asof":asof.isoformat(),"status":"READY_RESEARCH",
        "model_kind":model,"source_config_version":city+"-v1",
        "source_event_ms":int(observed.timestamp()*1000),
        "source_received_at":(asof-timedelta(seconds=30)).isoformat(),
        "predicted_index_f":55.,"sigma_f":1.,
        "markets":[{
            "ticker":event+"-T54.99","threshold_f":55.,
            "probability_yes":.63,
            "market_metadata_received_at":(asof-timedelta(minutes=2)).isoformat(),
            "close_time_utc":(target-timedelta(minutes=1)).isoformat(),
        }]
    }


def seed(db,row):
    with sqlite3.connect(db) as conn:
        _init(conn)
        source={"t":row["source_event_ms"],"v":54.,"status":"normal","contributors":5}
        conn.execute("""INSERT OR IGNORE INTO weather_hourly_index
            (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
            VALUES(?,?,?,?,?,?,?,?)""",
            (row["city"],row["source_event_ms"],row["source_received_at"],
             54.,"normal",row["source_config_version"],"a"*64,
             json.dumps(source,sort_keys=True)))


def exchange(target,city="chicago",settlement_f=56.,strike=55.,outcome="yes",
             wrong_event=False,wrong_target=False):
    event,_=event_for(city,target-timedelta(minutes=30))
    if wrong_event:
        event=event+"WRONG"
    index_time=target+timedelta(minutes=1) if wrong_target else target
    market={
        "ticker":event+"-T54.99","event_ticker":event,"status":"settled",
        "result":outcome,"yes_sub_title":f"{strike}° or above",
        "floor_strike":strike-.01,
        "rules_primary":"Resolves based on Kalshi published Weather Index.",
        "settlement_ts":(target+timedelta(minutes=6)).isoformat(),
    }
    index={"city":city,"units":"fahrenheit","config_version":city+"-v1",
           "timeseries":[{"t":int(index_time.timestamp()*1000),
                         "v":settlement_f,"status":"normal","contributors":5}]}
    def callback(request):
        if "/live_data/weather/" in request.url.path:
            return httpx.Response(200,json=index)
        return httpx.Response(200,json={"markets":[market],"cursor":""})
    return httpx.MockTransport(callback)


def do_settle(db,target,**kw):
    async def run():
        async with httpx.AsyncClient(transport=exchange(target,**kw)) as client:
            return await settle_forward_once(
                db,client=client,clock=lambda:target+timedelta(minutes=12))
    return asyncio.run(run())


def test_five_distinct_lead_buckets_and_first_snapshot_never_overwritten(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    target=datetime(2026,11,27,18,tzinfo=UTC)
    for lead in (60,45,30,15,5):
        asof=target-timedelta(minutes=lead)
        row=sample(asof=asof,lead=lead)
        seed(db,row)
        report=journal_forward(db,asof=asof,forecasts=[row])
        assert report["inserted"]==1
    with sqlite3.connect(db) as conn:
        rows=conn.execute("""SELECT lead_bucket,forecast_json,forecast_sha
             FROM weather_hourly_forward_snapshots ORDER BY lead_bucket""").fetchall()
    assert [r[0] for r in rows]==[5,15,30,45,60]
    assert all(hashlib.sha256(r[1].encode()).hexdigest()==r[2] for r in rows)
    overwrite=sample(asof=target-timedelta(minutes=30),lead=30)
    overwrite["predicted_index_f"]=90.
    assert journal_forward(db,asof=target-timedelta(minutes=30),
                           forecasts=[overwrite])["inserted"]==0
    with sqlite3.connect(db) as conn:
        original=json.loads(conn.execute("""SELECT forecast_json
            FROM weather_hourly_forward_snapshots WHERE lead_bucket=30""").fetchone()[0])
    assert original["predicted_index_f"]==55.
    assert original["model_kind"]=="HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8"


def test_no_post_close_future_receipt_missing_index_or_wrong_horizon_frozen(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    row=sample()
    seed(db,row)
    bad=[
        {**row,"source_received_at":(NOW+timedelta(minutes=1)).isoformat()},
        {**row,"markets":[{**row["markets"][0],
            "close_time_utc":(NOW-timedelta(seconds=1)).isoformat()}]},
        {**row,"markets":[{**row["markets"][0],"probability_yes":float("nan")}]},
        {**row,"source_config_version":"other-index-config"},
        {**row,"source_event_ms":int((NOW+timedelta(minutes=1)).timestamp()*1000)},
        {**row,"status":"STALE_INDEX"},
    ]
    for entry in bad:
        assert journal_forward(db,asof=NOW,forecasts=[entry])["inserted"]==0
    wrong=sample(asof=NOW,lead=30)
    wrong["target_utc"]=(NOW+timedelta(minutes=12)).isoformat()
    assert journal_forward(db,asof=NOW,forecasts=[wrong])["inserted"]==0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM weather_hourly_forward_snapshots").fetchone()[0]==0


def test_exact_top_hour_and_exact_ticker_market_settles_or_fails_closed(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    row=sample()
    seed(db,row)
    assert journal_forward(db,asof=NOW,forecasts=[row])["inserted"]==1
    target=datetime.fromisoformat(row["target_utc"])
    assert do_settle(db,target,wrong_target=True)["verified"]==0
    assert do_settle(db,target,wrong_event=True)["verified"]==0
    assert do_settle(db,target,strike=56.)["verified"]==0
    assert do_settle(db,target,settlement_f=54.,outcome="yes")["verified"]==0
    good=do_settle(db,target)
    assert good["verified"]==1
    assert do_settle(db,target)["verified"]==0
    report=forward_accuracy(tmp_path,db_path=db)
    group=report["groups"][0]
    assert group["verified_hours"]==1
    assert group["pending"]==0
    assert group["mae_f"] is None
    assert report["real_orders_enabled"] is False


def test_full_ten_distinct_hour_cohort_reports_only_its_own_metrics(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    target0=datetime(2026,11,25,12,tzinfo=UTC)
    # Every round uses a different actual event/target hour.
    for i in range(10):
        target=target0+timedelta(hours=i)
        asof=target-timedelta(minutes=30)
        row=sample(asof=asof,lead=30)
        seed(db,row)
        assert journal_forward(db,asof=asof,forecasts=[row])["inserted"]==1
        async def settle():
            async with httpx.AsyncClient(transport=exchange(target)) as client:
                return await settle_forward_once(
                    db,client=client,clock=lambda:target+timedelta(minutes=12))
        assert asyncio.run(settle())["verified"]==1

    # Unverified V7 city/lead cannot borrow the ten V8 Chicago outcomes.
    other=sample(city="nyc",asof=NOW,model="HISTORICAL_ADAPTIVE_INDEX_NOWCAST_V3")
    seed(db,other)
    assert journal_forward(db,asof=NOW,forecasts=[other])["inserted"]==1
    report=forward_accuracy(tmp_path,db_path=db)
    groups=report["groups"]
    primary=next(x for x in groups if x["city"]=="chicago" and x["lead_bucket"]==30)
    secondary=next(x for x in groups if x["city"]=="nyc")
    assert primary["verified_hours"]==10
    assert primary["mae_f"]==1.0
    assert primary["brier"]==pytest.approx(.1369)
    assert primary["log_loss"]==pytest.approx(-math.log(.63),abs=1e-6)
    assert primary["coverage_90"]==1.
    assert primary["model_kind"]=="HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8"
    assert secondary["verified_hours"]==0
    assert secondary["pending"]==1
    assert secondary["brier"] is None
    assert report["calibration_certified"] is False


def test_tampered_frozen_or_settled_source_never_masquerades_as_metrics(tmp_path):
    db=tmp_path/"weatheredge.sqlite3"
    row=sample()
    seed(db,row)
    journal_forward(db,asof=NOW,forecasts=[row])
    target=datetime.fromisoformat(row["target_utc"])
    assert do_settle(db,target)["verified"]==1
    with sqlite3.connect(db) as conn:
        conn.execute("""UPDATE weather_hourly_forward_outcomes
            SET market_raw='{}'""")
    invalid=forward_accuracy(tmp_path,db_path=db)
    assert invalid["status"]=="HOURLY_FORWARD_AUDIT_ERROR"
    assert invalid["groups"]==[]
    assert invalid["real_orders_enabled"] is False
