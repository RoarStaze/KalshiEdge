"""Prospective weather forecast journal: test-first, no simulated settlements."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from kalshi_edge.weather.prospective import (
    capture_station_snapshots, read_station_snapshots,
)


def make_station_db(path, t):
    path.parent.mkdir(parents=True, exist_ok=True)
    run=t-timedelta(hours=2)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY,station TEXT,source TEXT,event_time TEXT,received_at TEXT,payload TEXT)")
        for source in ("HRRR","NBM"):
            for lead in range(2,7):
                conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                    ("KORD",source,run.isoformat(),(run+timedelta(minutes=15)).isoformat(),
                    json.dumps({"model_run_at":run.isoformat(),
                                "forecast_valid_at":(run+timedelta(hours=lead)).isoformat(),
                                "temperature_k":285.15+lead})))
        event=t-timedelta(minutes=15)
        conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
            ("KORD","METAR",event.isoformat(),(event+timedelta(minutes=2)).isoformat(),
             json.dumps({"temp_f":62.0})))
    return path


def test_frozen_forecast_snapshot_once_per_local_hour_not_overwritten(tmp_path):
    # Nov 19 18:20Z is 12:20 local standard time at KORD.
    t=datetime(2026,11,19,18,20,tzinfo=timezone.utc)
    db=make_station_db(tmp_path/"weather"/"weatheredge.sqlite3",t)
    first=capture_station_snapshots(db,asof=t,stations=("KORD",))
    assert first["inserted"]==1
    before=read_station_snapshots(db,"KORD")
    assert len(before)==1
    snap=before[0]
    assert snap["climate_day"]=="2026-11-19"
    assert snap["checkpoint_hour_standard"]==12
    assert snap["asof"]==t.isoformat()
    assert snap["forecast"]["model_kind"]=="BASELINE_UNCALIBRATED"
    assert snap["forecast"]["mean_f"] is not None
    assert snap["forecast"]["tradable"] is False
    assert snap["forecast"]["metar_received_at"] <= t.isoformat()
    assert all(x <= t.isoformat() for x in snap["forecast"]["nwp_receipts_utc"].values())
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE samples SET payload=? WHERE source='METAR'",
                     (json.dumps({"temp_f":100.0}),))
    repeat=capture_station_snapshots(db,asof=t+timedelta(minutes=20),stations=("KORD",))
    assert repeat["inserted"]==0
    assert read_station_snapshots(db,"KORD")==before


def test_no_future_or_missing_noon_forecast_is_fabricated(tmp_path):
    t=datetime(2026,11,19,18,20,tzinfo=timezone.utc)
    db=tmp_path/"weatheredge.sqlite3"
    out=capture_station_snapshots(db,asof=t,stations=("KORD",))
    assert out["inserted"]==0
    assert out["statuses"]["KORD"]=="MISSING_DATABASE"
    assert not db.exists()


def test_other_stations_and_out_of_hours_do_not_generate_fake_samples(tmp_path):
    t=datetime(2026,11,19,18,20,tzinfo=timezone.utc)
    db=make_station_db(tmp_path/"weatheredge.sqlite3",t)
    status=capture_station_snapshots(db,asof=t,stations=("KORD","KDEN"))
    assert status["inserted"]==1
    assert status["statuses"]["KDEN"] != "READY_RESEARCH"
    # Midnight standard time is not a planned same-day peak forecast checkpoint.
    late=capture_station_snapshots(db,asof=t+timedelta(hours=12),stations=("KORD",))
    assert late["inserted"]==0
    assert late["statuses"]["KORD"]=="OUTSIDE_FORECAST_WINDOW"



def test_managed_weather_worker_records_prospective_snapshots_without_user_commands(tmp_path):
    import asyncio
    import json
    from kalshi_edge.weather.runtime import WeatherSupervisor

    received=[]
    def capture(db):
        received.append(db)
        return {"mode":"PAPER_ONLY","inserted":1,"statuses":{"KORD":"READY_RESEARCH"}}

    worker=WeatherSupervisor(
        tmp_path/"weatheredge.sqlite3",
        metar_collect=lambda db: None,
        nwp_collect=lambda db: None,
        ensure_decoder=lambda:{"state":"AVAILABLE"},
        forecast_capture=capture,
    )
    asyncio.run(worker.poll_forecasts())
    assert received==[tmp_path/"weatheredge.sqlite3"]
    health=json.loads((tmp_path/"collector-status.json").read_text())
    assert health["forecast_journal"]["state"]=="READY"
    assert health["forecast_journal"]["inserted"]==1
    assert health["real_orders_enabled"] is False
