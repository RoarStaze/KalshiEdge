"""WeatherEdge History: durable first-write verdicts, real accuracy, and BTC-style UI."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.weather.hourly import _init, event_for
from kalshi_edge.weather.hourly_history import read_hourly_history, sync_hourly_history

UTC=timezone.utc
BASE=datetime(2026,11,27,18,tzinfo=UTC)


def seed(db: Path, *, city="chicago", hour=0, probability=.8,
         settled="yes", actual=56.0, ticker_suffix="-T54.99",
         tamper=False, close_known=True):
    target=BASE+timedelta(hours=hour)
    asof=target-timedelta(minutes=25)
    event,expected=event_for(city,asof)
    assert expected==target
    ticker=event+ticker_suffix
    close=(asof+timedelta(minutes=8)).isoformat() if close_known else None
    selected={"ticker":ticker,"threshold_f":55.0,
              "probability_yes":probability,"close_time_utc":close,
              "market_metadata_received_at":(asof-timedelta(minutes=3)).isoformat()}
    frozen={"city":city,"event_ticker":event,"target_utc":target.isoformat(),
            "asof":asof.isoformat(),"source":"KALSHI_WEATHER_INDEX",
            "source_config_version":"hourly-index-v1",
            "status":"READY_RESEARCH","selected_market":selected,
            "model_kind":"HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8",
            "predicted_index_f":54.8,"sigma_f":1.5,
            "source_event_ms":int((asof-timedelta(minutes=2)).timestamp()*1000),
            "source_received_at":(asof-timedelta(minutes=1)).isoformat()}
    serialized=json.dumps(frozen,sort_keys=True,allow_nan=False)
    idx={"city":city,"units":"fahrenheit","config_version":"hourly-index-v1",
         "timeseries":[{"t":int(target.timestamp()*1000),"v":actual,
                        "status":"normal","contributors":5}]}
    idx_raw=json.dumps(idx,sort_keys=True)
    market={"ticker":ticker,"event_ticker":event,"status":"settled",
            "result":settled,"yes_sub_title":"55° or above",
            "floor_strike":54.99,"rules_primary":"Kalshi Weather Index",
            "settlement_ts":(target+timedelta(minutes=6)).isoformat()}
    market_raw=json.dumps({"markets":[market],"cursor":""},sort_keys=True)
    with sqlite3.connect(db) as c:
        _init(c)
        c.execute("""INSERT INTO weather_hourly_snapshots
         (city,event_ticker,market_ticker,asof,target_utc,source_reading_ms,
          source_receipt,forecast_json,forecast_sha)
         VALUES(?,?,?,?,?,?,?,?,?)""",
         (city,event,ticker,asof.isoformat(),target.isoformat(),
          frozen["source_event_ms"],frozen["source_received_at"],serialized,
          hashlib.sha256(serialized.encode()).hexdigest()))
        if settled is not None:
            c.execute("""INSERT INTO weather_hourly_settlements
             (city,event_ticker,market_ticker,result,observed_index_f,
              observed_event_ms,checked_at,source_index_sha,market_sha,
              source_index_raw,market_raw) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
             (city,event,ticker,settled,actual,int(target.timestamp()*1000),
              (target+timedelta(minutes=12)).isoformat(),
              hashlib.sha256(idx_raw.encode()).hexdigest(),
              hashlib.sha256(market_raw.encode()).hexdigest(),
              idx_raw,market_raw))
    return event


def test_persistent_correct_incorrect_skip_pending_and_accuracy(tmp_path):
    db=tmp_path/"weather.sqlite3"
    seed(db,hour=0,probability=.86,settled="yes",actual=56)
    seed(db,hour=1,probability=.77,settled="no",actual=54)
    seed(db,hour=2,probability=.5,settled="no",actual=54)
    seed(db,hour=3,probability=.91,settled=None)
    report=sync_hourly_history(db)
    assert report["recorded"]==3
    assert sync_hourly_history(db)["recorded"]==0
    with sqlite3.connect(db) as c:
        ledger=c.execute("""SELECT verdict FROM weather_hourly_history_verdicts
           ORDER BY event_ticker""").fetchall()
    assert {r[0] for r in ledger}=={"CORRECT","INCORRECT","SKIP"}
    result=read_hourly_history(tmp_path,db_path=db)
    s=result["summary"]
    assert s["total_final_forecasts"]==4
    assert (s["correct"],s["incorrect"],s["skipped"],s["pending_settlement"])==(1,1,1,1)
    assert s["settled_directional_forecasts"]==2
    assert s["accuracy"]==.5
    assert result["real_orders_enabled"] is False
    assert result["status"]=="READY"
    assert [r["verdict"] for r in result["forecasts"]]==[
        "PENDING","SKIP","INCORRECT","CORRECT"]
    win=next(r for r in result["forecasts"] if r["verdict"]=="CORRECT")
    assert win["forecast"]=="ABOVE"
    assert win["actual_side"]=="ABOVE"
    assert win["directional_probability"]==.86
    assert win["confidence"]==pytest.approx(.72)
    assert win["market_close"].endswith("+00:00")
    assert win["model_kind"]=="HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8"


def test_no_fabricated_accuracy_or_market_close_and_city_filter(tmp_path):
    db=tmp_path/"weather.sqlite3"
    seed(db,city="chicago",hour=0,probability=.81,settled=None,close_known=False)
    seed(db,city="nyc",hour=1,probability=.2,settled="no",actual=54)
    sync_hourly_history(db)
    chicago=read_hourly_history(tmp_path,db_path=db,city="chicago")
    assert chicago["summary"]["accuracy"] is None
    assert chicago["summary"]["pending_settlement"]==1
    assert chicago["forecasts"][0]["market_close"] is None
    assert chicago["forecasts"][0]["target_utc"]
    nyc=read_hourly_history(tmp_path,db_path=db,city="nyc")
    assert nyc["summary"]["accuracy"]==1.
    assert nyc["summary"]["correct"]==1
    assert nyc["forecasts"][0]["forecast"]=="BELOW"
    assert nyc["forecasts"][0]["actual_side"]=="BELOW"
    assert nyc["forecasts"][0]["directional_probability"]==.8
    assert nyc["forecasts"][0]["confidence"]==pytest.approx(.6)
    assert read_hourly_history(tmp_path,db_path=db,city="all")["summary"]["correct"]==1


def test_verdict_cannot_be_rewritten_and_source_tampering_fails_closed(tmp_path):
    db=tmp_path/"weather.sqlite3"
    seed(db,hour=0,probability=.75,settled="no",actual=54.)
    assert sync_hourly_history(db)["recorded"]==1
    with sqlite3.connect(db) as c:
        c.execute("UPDATE weather_hourly_history_verdicts SET verdict='CORRECT'")
    result=read_hourly_history(tmp_path,db_path=db)
    assert result["status"]=="HOURLY_HISTORY_AUDIT_ERROR"
    assert result["summary"]["correct"]==0
    assert result["summary"]["accuracy"] is None
    assert result["forecasts"]==[]
    # Source tampering also fails closed even with the original ledger.
    with sqlite3.connect(db) as c:
        c.execute("UPDATE weather_hourly_history_verdicts SET verdict='INCORRECT'")
        c.execute("UPDATE weather_hourly_settlements SET source_index_raw='{}'")
    assert read_hourly_history(tmp_path,db_path=db)["status"]=="HOURLY_HISTORY_AUDIT_ERROR"
    assert sync_hourly_history(db)["recorded"]==0


def test_conflicting_market_or_ticker_never_records_a_false_verdict(tmp_path):
    db=tmp_path/"weather.sqlite3"
    seed(db,hour=0,probability=.8,settled="yes",actual=54.)
    seed(db,hour=1,probability=.8,settled="yes",actual=56.)
    with sqlite3.connect(db) as c:
        c.execute("""UPDATE weather_hourly_settlements SET market_ticker='OTHER'
         WHERE event_ticker=(SELECT event_ticker FROM weather_hourly_snapshots
                            ORDER BY target_utc DESC LIMIT 1)""")
    sync=sync_hourly_history(db)
    assert sync["recorded"]==0
    assert sync["unverified"]==2
    history=read_hourly_history(tmp_path,db_path=db)
    assert history["summary"]["correct"]==0
    assert history["summary"]["incorrect"]==0
    # An actually conflicting source/ticker must invalidate the entire
    # audited score, rather than silently promote suspect rows as settled.
    assert history["status"]=="HOURLY_HISTORY_AUDIT_ERROR"
    assert history["summary"]["accuracy"] is None
    assert history["forecasts"]==[]


def test_filter_and_pagination_do_not_change_global_summary(tmp_path):
    db=tmp_path/"weather.sqlite3"
    for i in range(8):
        seed(db,hour=i,probability=.7,settled="yes" if i%2==0 else "no",
             actual=56 if i%2==0 else 54)
    sync_hourly_history(db)
    page=read_hourly_history(tmp_path,db_path=db,verdict="CORRECT",limit=2,offset=1)
    assert page["summary"]["accuracy"]==.5
    assert page["filtered_count"]==4
    assert len(page["forecasts"])==2
    assert all(x["verdict"]=="CORRECT" for x in page["forecasts"])
    assert page["next_offset"]==3
    with pytest.raises(ValueError):
        read_hourly_history(tmp_path,db_path=db,verdict="BOGUS")
    with pytest.raises(ValueError):
        read_hourly_history(tmp_path,db_path=db,limit=100000)
    with pytest.raises(ValueError):
        read_hourly_history(tmp_path,db_path=db,city="not-a-city")


def test_bitcoin_style_weather_history_has_summary_table_and_live_source():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("history","")
    for item in ("Settled forecast accuracy","Correct / Incorrect",
                 "SKIP decisions","Pending settlement","Recorded weather forecasts",
                 "Market closes (local)","Market","Forecast","Probability",
                 "Confidence","Settled outcome","Result",
                 'id="wx-history-accuracy"','id="wx-history-correctness"',
                 'id="wx-history-skips"','id="wx-history-pending"',
                 'id="wx-history-filter"','id="wx-history-city"',
                 'id="wx-history-rows"','id="wx-history-status"',
                 '/api/weather/hourly/history'):
        assert item in html
    assert "id=\"wx-trade-grid\"" not in html
    assert 'id="wx-hourly-city"' not in html
    assert "PAPER ONLY" in html
    assert "/api/manual-trade/" not in html
    assert 'setInterval(refreshWeatherHistory,15000)' in html


def test_live_and_preview_history_api_read_only(tmp_path):
    from kalshi_edge.bootstrap.final_history import _make_handler
    from kalshi_edge.bootstrap.preview_site import make_preview_handler
    for preview in (False,True):
        factory=(make_preview_handler() if preview else
                 _make_handler(tmp_path/"bootstrap",credential_vault=SimpleNamespace(exists=lambda:True)))
        server=ThreadingHTTPServer(("127.0.0.1",0),factory)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            base=f"http://127.0.0.1:{server.server_port}"
            with httpx.Client(trust_env=False,timeout=5) as client:
                url=base+"/api/weather/hourly/history"
                r=client.get(url)
                assert r.status_code==200
                payload=r.json()
                assert payload["real_orders_enabled"] is False
                assert payload["summary"]["accuracy"] is None
                assert payload["forecasts"]==[]
                assert client.post(url,json={}).status_code in (403,404)
                assert client.get(url+"?city=nonexistent").status_code==400
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


def test_weather_worker_persists_verified_verdicts_automatically(tmp_path,monkeypatch):
    from kalshi_edge.weather import runtime
    db=tmp_path/"weather.sqlite3"
    called=[]
    def sync(path):
        called.append(path)
        return {"mode":"HOURLY_HISTORY_READ_ONLY","recorded":2,
                "unverified":0,"real_orders_enabled":False}
    async def settled(_):
        return {"mode":"HOURLY_PAPER_ONLY","verified":0,"unverified":0}
    async def forward(_):
        return {"mode":"HOURLY_FORWARD_READ_ONLY",
                "verified":0,"unverified":0,"real_orders_enabled":False}
    monkeypatch.setattr(runtime,"sync_hourly_history",sync,raising=False)
    monkeypatch.setattr(runtime,"settle_forward_once",forward)
    worker=runtime.WeatherSupervisor(db,hourly_settle=settled)
    asyncio.run(worker.poll_hourly_settlements())
    assert called==[db]
    health=json.loads((db.parent/"collector-status.json").read_text())
    assert health["hourly_settlement"]["history"]["recorded"]==2
    assert health["real_orders_enabled"] is False
