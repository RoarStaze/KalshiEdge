"""15-minute airport forecast tests; never imply a 15m Kalshi weather market."""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.weather.short_horizon import (
    read_quarter_hour_forecast, read_quarter_hour_forecasts,
)
from kalshi_edge.weather.short_horizon_journal import (
    capture_quarter_hour_snapshots, score_quarter_hour_snapshots,
)


NOW = datetime(2026,11,22,18,20,tzinfo=timezone.utc)


def _fixture_db(tmp_path, *, remove_model=None, omit_latest=False, late_latest=False):
    db=tmp_path/"data"/"weather"/"weatheredge.sqlite3"
    db.parent.mkdir(parents=True)
    run=NOW.replace(hour=17,minute=0)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE samples(id INTEGER PRIMARY KEY,station TEXT,"
            "source TEXT,event_time TEXT,received_at TEXT,payload TEXT)"
        )
        for name in ("HRRR","NBM"):
            if name==remove_model:
                continue
            for lead,k in ((1,285.0),(2,286.0)):
                event=(run+timedelta(hours=lead))
                conn.execute(
                    "INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                    ("KMDW",name,run.isoformat(),(run+timedelta(minutes=19)).isoformat(),
                     json.dumps({"model_run_at":run.isoformat(),
                                 "forecast_valid_at":event.isoformat(),
                                 "temperature_k":k})),
                )
        for minutes,temperature in ((20,60.0),(5,62.0)):
            if minutes==5 and omit_latest:
                continue
            event=NOW-timedelta(minutes=minutes)
            received=event+timedelta(minutes=2 if not late_latest or minutes!=5 else 6)
            conn.execute(
                "INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                ("KMDW","METAR",event.isoformat(),received.isoformat(),
                 json.dumps({"temp_f":temperature})),
            )
    return db


def test_quarter_hour_predicts_actual_15min_target_with_normalized_directions(tmp_path):
    db=_fixture_db(tmp_path)
    result=read_quarter_hour_forecast(db,"KMDW",NOW)
    assert result["status"]=="READY_RESEARCH"
    assert result["asof"]==NOW.isoformat()
    assert result["target_at"]==(NOW+timedelta(minutes=15)).isoformat()
    assert result["latest_observed_f"]==62.
    assert result["mean_f"] is not None
    assert result["interval_90_f"][0]<result["mean_f"]<result["interval_90_f"][1]
    assert abs(sum(result["direction_probabilities"].values())-1)<1e-9
    assert set(result["direction_probabilities"])=={"rise_at_least_half_f","fall_at_least_half_f","within_half_f"}
    assert result["model_kind"]=="UNCALIBRATED_OBS_NWP_DELTA"
    assert result["tradable"] is False
    assert result["settlement_verified"] is False
    assert result["settlement_target"]=="AIRPORT_METAR_PROXY_NOT_KALSHI_INDEX"
    assert result["metar_received_at"]<=NOW.isoformat()
    assert all(t<=NOW.isoformat() for t in result["nwp_receipts_utc"].values())


def test_late_observation_does_not_leak_into_point_in_time_result(tmp_path):
    db=_fixture_db(tmp_path,late_latest=True)
    result=read_quarter_hour_forecast(db,"KMDW",NOW)
    assert result["status"]=="INSUFFICIENT_OBSERVATION_HISTORY"
    assert result["mean_f"] is None


def test_no_incomplete_forecast_cycle_mixing(tmp_path):
    db=_fixture_db(tmp_path,remove_model="NBM")
    result=read_quarter_hour_forecast(db,"KMDW",NOW)
    assert result["status"]=="INCOMPLETE_NBM"
    assert result["source_statuses"]["HRRR"]=="READY"
    assert result["source_statuses"]["NBM"]=="INCOMPLETE_NBM"
    assert result["mean_f"] is None


def test_old_metar_is_not_a_live_15min_forecast(tmp_path):
    db=_fixture_db(tmp_path)
    result=read_quarter_hour_forecast(db,"KMDW",NOW+timedelta(minutes=40))
    assert result["status"]=="STALE_METAR"
    assert result["mean_f"] is None


def test_preview_never_reads_local_weather_data(tmp_path):
    root=tmp_path/"data"/"bootstrap"
    db=_fixture_db(tmp_path)
    preview=read_quarter_hour_forecasts(root,preview=True)
    assert preview["mode"]=="PAPER_ONLY"
    assert preview["market_exists_15m"] is False
    assert all(row["status"]=="PREVIEW_NO_DATA" for row in preview["forecasts"])
    assert all(row["mean_f"] is None for row in preview["forecasts"])


def test_journal_is_write_once_per_station_5min_window_and_labels_only_observation(tmp_path):
    db=_fixture_db(tmp_path)
    a=capture_quarter_hour_snapshots(db,asof=NOW,stations=("KMDW",))
    b=capture_quarter_hour_snapshots(db,asof=NOW+timedelta(minutes=2),stations=("KMDW",))
    assert a["inserted"]==1
    assert b["inserted"]==0
    report=score_quarter_hour_snapshots(db,station="KMDW",asof=NOW)
    assert report["matched_forecasts"]==0
    with sqlite3.connect(db) as conn:
        event=NOW+timedelta(minutes=15)
        conn.execute(
            "INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
            ("KMDW","METAR",event.isoformat(),
             (event+timedelta(minutes=2)).isoformat(),json.dumps({"temp_f":64.})),
        )
    scored=score_quarter_hour_snapshots(db,station="KMDW",
                                       asof=NOW+timedelta(hours=1))
    assert scored["matched_forecasts"]==1
    assert scored["mae_f"]>=0
    assert scored["interval_90_observed_coverage"] in (0.,1.)
    assert scored["label_kind"]=="AIRPORT_METAR_PROXY_NOT_MARKET_SETTLEMENT"
    assert scored["independently_calibrated"] is False


def test_missing_database_cannot_create_fake_forecasts(tmp_path):
    db=tmp_path/"missing.sqlite3"
    status=read_quarter_hour_forecast(db,"KMDW",NOW)
    assert status["status"]=="MISSING_DATABASE"
    result=capture_quarter_hour_snapshots(db,asof=NOW,stations=("KMDW",))
    assert result["inserted"]==0
    assert not db.exists()


def test_new_weather_page_and_read_only_api_explain_contract_limitations():
    from kalshi_edge.weather.research_ui import render_weather_page
    html=render_weather_page("15m")
    assert "/api/weather/15m" in html
    assert "NOT A KALSHI 15-MINUTE CONTRACT" in html
    assert 'id="wx-15m-station"' in html
    assert "/api/manual-trade/" not in html



def test_existing_one_command_supervisor_automatically_records_short_outlooks(tmp_path):
    from kalshi_edge.weather.runtime import WeatherSupervisor
    db=tmp_path/"weatheredge.sqlite3"
    seen=[]
    def collector(path):
        seen.append(path)
        return {"mode":"PAPER_ONLY","inserted":1,"statuses":{"KMDW":"READY_RESEARCH"}}
    worker=WeatherSupervisor(
        db, ensure_decoder=lambda:{"state":"AVAILABLE"},
        quarter_capture=collector,
    )
    asyncio.run(worker.poll_quarter_hour())
    assert seen==[db]
    health=json.loads((db.parent/"collector-status.json").read_text())
    assert health["quarter_hour"]["state"]=="READY"
    assert health["quarter_hour"]["inserted"]==1
    assert health["real_orders_enabled"] is False


def test_weather_15m_get_endpoint_real_and_preview_do_not_send_orders(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    import httpx
    from kalshi_edge.bootstrap.final_history import _make_handler
    from kalshi_edge.bootstrap.preview_site import make_preview_handler

    for preview in (False, True):
        handler=(make_preview_handler() if preview else
            _make_handler(tmp_path/"bootstrap",credential_vault=SimpleNamespace(exists=lambda:True)))
        server=ThreadingHTTPServer(("127.0.0.1",0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with httpx.Client(timeout=5,trust_env=False) as client:
                root=f"http://127.0.0.1:{server.server_port}"
                response=client.get(root+"/api/weather/15m")
                assert response.status_code==200
                payload=response.json()
                assert payload["mode"]=="PAPER_ONLY"
                assert payload["horizon_minutes"]==15
                assert payload["market_exists_15m"] is False
                assert all(item["tradable"] is False for item in payload["forecasts"])
                if preview:
                    assert all(item["mean_f"] is None for item in payload["forecasts"])
                assert client.post(root+"/api/weather/15m",json={}).status_code in (403,404)
                page=client.get(root+"/weather/15m")
                assert page.status_code==200
                assert "NOT A KALSHI 15-MINUTE CONTRACT" in page.text
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
