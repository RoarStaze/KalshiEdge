"""Point-in-time WeatherEdge probability and training contracts."""
from __future__ import annotations

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

from kalshi_edge.weather.predictor import (
    read_station_forecast, normal_bucket_probabilities, STATIONS,
)
from kalshi_edge.weather.training import fit_station_model, train_model_file


def _sample(conn, station, source, event, receipt, payload):
    conn.execute(
        "INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
        (station,source,event.isoformat(),receipt.isoformat(),json.dumps(payload)),
    )


def _data(tmp_path, *, late=False, incomplete=False, stale=False):
    db=tmp_path/"weather"/"weatheredge.sqlite3"
    db.parent.mkdir(parents=True)
    conn=sqlite3.connect(db)
    conn.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY,station TEXT,source TEXT,event_time TEXT,received_at TEXT,payload TEXT)")
    T=datetime(2026,11,19,18,tzinfo=timezone.utc)  # noon at ORD, standard time
    run=T-timedelta(hours=2)
    for source in ("HRRR","NBM"):
        for hour in (2,3,4,5,6):
            valid=run+timedelta(hours=hour)
            _sample(conn,"KORD",source,run,run+timedelta(minutes=15),
                    {"model_run_at":run.isoformat(),"forecast_valid_at":valid.isoformat(),
                     "temperature_k":285.15+hour})
        if incomplete:
            new_run=T-timedelta(hours=1)
            _sample(conn,"KORD",source,new_run,new_run+timedelta(minutes=10),
                    {"model_run_at":new_run.isoformat(),"forecast_valid_at":(new_run+timedelta(hours=4)).isoformat(),
                     "temperature_k":330.15})
    obs=T-timedelta(minutes=25 if not stale else 150)
    _sample(conn,"KORD","METAR",obs,obs+timedelta(minutes=3),{"temp_f":60.0})
    old=T-timedelta(hours=2)
    _sample(conn,"KORD","METAR",old,old+timedelta(minutes=4),{"temp_f":57.0})
    if late:
        _sample(conn,"KORD","METAR",T-timedelta(minutes=12),
                T+timedelta(minutes=10),{"temp_f":101.0})
    conn.commit()
    conn.close()
    return db,T


def test_station_specs_restrict_to_configured_airports():
    assert set(STATIONS)=={"KORD","KMDW","KDEN","KATL","KLAX"}
    assert STATIONS["KORD"].standard_offset_hours==-6
    assert STATIONS["KMDW"].kalshi_series=="KXHIGHCHI"
    assert STATIONS["KORD"].kalshi_series is None


def test_point_in_time_weather_forecast_excludes_late_metar(tmp_path):
    db,T=_data(tmp_path,late=True)
    result=read_station_forecast(db,"KORD",T)
    assert result["status"]=="READY_RESEARCH"
    assert result["mode"]=="PAPER_ONLY"
    assert result["model_kind"]=="BASELINE_UNCALIBRATED"
    assert result["observed_max_f"]==60.0
    assert result["mean_f"]<90
    assert result["settlement_verified"] is False
    assert result["tradable"] is False
    assert 0<result["sigma_f"]<20
    assert math.isclose(sum(x["probability"] for x in result["buckets"]),1.0,abs_tol=1e-10)


def test_incomplete_new_nwp_cycle_does_not_mix_with_older_complete_cycle(tmp_path):
    db,T=_data(tmp_path,incomplete=True)
    result=read_station_forecast(db,"KORD",T)
    assert result["status"]=="READY_RESEARCH"
    assert result["hrrr_max_f"]<100
    assert result["nbm_max_f"]<100


def test_stale_metar_blocks_forecast(tmp_path):
    db,T=_data(tmp_path,stale=True)
    result=read_station_forecast(db,"KORD",T)
    assert result["status"]=="STALE_METAR"
    assert result["mean_f"] is None
    assert result["buckets"]==[]


def test_missing_and_unknown_station_fail_closed(tmp_path):
    db,T=_data(tmp_path)
    assert read_station_forecast(db,"KDEN",T)["mean_f"] is None
    with pytest.raises(ValueError):
        read_station_forecast(db,"../../secrets",T)


def test_too_old_model_cycle_is_not_a_forecast(tmp_path):
    db,T=_data(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE samples SET event_time=?,received_at=? WHERE source='NBM'",
                     ((T-timedelta(hours=10)).isoformat(),(T-timedelta(hours=9)).isoformat()))
    result=read_station_forecast(db,"KORD",T)
    assert result["status"] in {"STALE_NBM","INCOMPLETE_NBM"}
    assert result["mean_f"] is None


def test_bad_model_artifact_blocks_prediction(tmp_path):
    db,T=_data(tmp_path)
    model=tmp_path/"models"/"KORD.json"
    model.parent.mkdir()
    model.write_text("{invalid")
    result=read_station_forecast(db,"KORD",T,model_dir=model.parent)
    assert result["status"]=="INVALID_MODEL"
    assert result["mean_f"] is None


def test_temperature_degree_bins_normalize_over_all_tails():
    values=normal_bucket_probabilities(72.0,3.0)
    assert len(values)==23
    assert all(0<=x["probability"]<=1 for x in values)
    assert math.isclose(sum(x["probability"] for x in values),1.0,abs_tol=1e-12)


def _training_rows(days=80,station="KORD"):
    T=datetime(2026,1,1,18,tzinfo=timezone.utc)
    rows=[]
    for day in range(days):
        stamp=T+timedelta(days=day)
        base=58+(day%11)
        rows.append({
           "station":station,"asof":stamp.isoformat(),
           "settlement_day":stamp.date().isoformat(),
           "hrrr_max_f":base+2,"nbm_max_f":base-1,
           "observed_max_f":base-3,"trend_f_per_hour":0.5,
           "hours_to_peak":4,
           "actual_max_f":base+1+(day%3)*0.2,
           "label_source":"FIRST_PARTY_REVIEW_REQUIRED",
        })
    return rows


def test_station_training_needs_distinct_days_and_has_strict_holdout(tmp_path):
    rows=_training_rows()
    trained=fit_station_model(rows,"KORD",min_days=60)
    assert trained["station"]=="KORD"
    assert trained["train_days"]+trained["holdout_days"]==80
    assert trained["train_end_day"]<trained["holdout_start_day"]
    assert trained["model_kind"]=="STATION_BIAS_GAUSSIAN_RESEARCH"
    assert trained["settlement_verified"] is False
    assert math.isfinite(trained["holdout"]["log_loss"])
    assert math.isfinite(trained["holdout"]["baseline_log_loss"])
    out=tmp_path/"models"/"KORD.json"
    inp=tmp_path/"examples.jsonl"
    inp.write_text("\n".join(json.dumps(x) for x in rows))
    train_model_file(inp,"KORD",out,min_days=60)
    saved=json.loads(out.read_text())
    assert saved["train_days"]==trained["train_days"]


def test_training_rejects_insufficient_days_and_bad_labels():
    with pytest.raises(ValueError,match="independent"):
        fit_station_model(_training_rows(59),"KORD",min_days=60)
    bad=_training_rows()
    bad[0]["actual_max_f"]=float("nan")
    with pytest.raises(ValueError):
        fit_station_model(bad,"KORD")


def test_weather_forecast_endpoint_is_read_only(tmp_path):
    from kalshi_edge.bootstrap.final_history import _make_handler
    handler=_make_handler(tmp_path/"bootstrap",credential_vault=SimpleNamespace(exists=lambda:True))
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    runner=threading.Thread(target=server.serve_forever,daemon=True)
    runner.start()
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5,trust_env=False) as client:
            response=client.get(base+"/api/weather/forecast")
            assert response.status_code==200
            data=response.json()
            assert data["mode"]=="PAPER_ONLY"
            assert data["real_orders_enabled"] is False
            assert len(data["forecasts"])==5
            assert all(x["mean_f"] is None for x in data["forecasts"])
            assert client.post(base+"/api/weather/forecast",json={}).status_code in (403,404)
    finally:
        server.shutdown();server.server_close();runner.join(timeout=3)


def test_weather_page_shows_live_station_forecasting_without_manual_orders():
    from kalshi_edge.weather.research_ui import render_weather_page
    page=render_weather_page("forecast")
    assert "wx-forecast-station" in page
    assert "wx-forecast-buckets" in page
    assert "/api/weather/forecast" in page
    assert "/api/manual-trade/" not in page



def test_incomplete_nbm_keeps_hrrr_and_metar_source_diagnostics(tmp_path):
    # A missing NBM field must not make successfully decoded HRRR and METAR
    # observations disappear from the dashboard. No fake probability is shown.
    db, asof = _data(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM samples WHERE source='NBM'")
    result = read_station_forecast(db, "KORD", asof)
    assert result["status"] == "INCOMPLETE_NBM"
    assert result["observed_max_f"] == 60.0
    assert result["hrrr_max_f"] is not None
    assert result["nwp_cycles_utc"]["HRRR"]
    assert result["nwp_receipts_utc"]["HRRR"]
    assert result["metar_event_at"]
    assert result["metar_received_at"]
    assert result["source_statuses"]["METAR"] == "READY"
    assert result["source_statuses"]["HRRR"] == "READY"
    assert result["source_statuses"]["NBM"] == "INCOMPLETE_NBM"
    assert result["mean_f"] is None
    assert result["buckets"] == []


def test_no_nwp_inputs_report_individual_source_failures(tmp_path):
    db, asof = _data(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM samples WHERE source IN ('HRRR', 'NBM')")
    result = read_station_forecast(db, "KORD", asof)
    assert result["status"] == "INCOMPLETE_HRRR"
    assert result["source_statuses"]["METAR"] == "READY"
    assert result["source_statuses"]["HRRR"] == "INCOMPLETE_HRRR"
    assert result["source_statuses"]["NBM"] == "INCOMPLETE_NBM"
    assert result["mean_f"] is None



def test_weather_dashboard_explains_missing_model_status_instead_of_blanks():
    from kalshi_edge.weather.research_ui import render_weather_page
    page = render_weather_page("forecast")
    assert 'id="wx-forecast-source-metar"' in page
    assert 'id="wx-forecast-source-hrrr"' in page
    assert 'id="wx-forecast-source-nbm"' in page
    assert 'id="wx-forecast-diagnostic"' in page
    assert "kalshiedge start" in page
    assert "Automatic collection" in page
    assert "wx-collector-health" in page
    assert "source_statuses" in page
    assert "/api/manual-trade/" not in page



def test_same_day_trained_artifact_is_not_usable_for_prior_noon_forecast(tmp_path):
    # An artifact fitted with today's final daily high would leak future
    # settlement into an afternoon forecast; require train_end < target day.
    db,asof=_data(tmp_path)
    model_dir=tmp_path/"models"
    model_dir.mkdir()
    artifact={
        "station":"KORD","model_kind":"STATION_BIAS_GAUSSIAN_RESEARCH",
        "bias_f":1.0,"sigma_f":3.0,"train_days":60,
        "train_end_day":"2026-11-19",
    }
    (model_dir/"KORD.json").write_text(json.dumps(artifact))
    result=read_station_forecast(db,"KORD",asof,model_dir=model_dir)
    assert result["status"]=="INVALID_MODEL"
    assert result["mean_f"] is None
