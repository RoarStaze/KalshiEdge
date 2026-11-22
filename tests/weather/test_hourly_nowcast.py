"""Regression coverage for point-in-time Kalshi Index nowcast promotion.

The fixtures reproduce published minute readings with local receipt stamps.
These tests validate the algorithm, not real-world forecasting superiority.
"""
from __future__ import annotations
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from kalshi_edge.weather.hourly import _init
from kalshi_edge.weather.hourly_nowcast import (
    index_cases, choose_index_nowcast, index_nowcast_decision,
    nowcast_prediction,
)

UTC = timezone.utc
START=datetime(2026,10,1,1,tzinfo=UTC)
CITY="chicago"
CONFIG="chicago-temperature-v1.1"


def populated(n=104, *, delayed=False, config=CONFIG):
    import json
    conn=sqlite3.connect(":memory:")
    _init(conn)
    # Each target is the exact top-of-hour canonical index reading.
    # Vary trends across hours without leaking a value to the prior hour.
    for hour in range(n):
        target=START+timedelta(hours=hour)
        slope=2.5+.3*math.sin(hour*.41)
        at_target=52+1.4*math.sin(hour*.19)+.02*hour
        for minute in range(-55,1,5):
            at=target+timedelta(minutes=minute)
            value=round(at_target+slope*minute/60,2)
            received=at+timedelta(minutes=2 if not delayed else 40)
            raw={"t":int(at.timestamp()*1000),"v":value,"status":"normal",
                 "contributors":5}
            conn.execute("""INSERT INTO weather_hourly_index
                  (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
                  VALUES(?,?,?,?,?,?,?,?)""",
                  (CITY,raw["t"],received.isoformat(),value,"normal",
                   config,"source-hash",json.dumps(raw,sort_keys=True)))
    conn.commit()
    return conn,START+timedelta(hours=n-1,minutes=12)


def test_hourly_index_nowcast_learns_only_from_observed_published_hours():
    conn,asof=populated()
    with conn:
        cases=index_cases(conn,CITY,asof,lead_minutes=30,config_version=CONFIG)
    assert len(cases)>=96
    assert all(c["target_utc"]<asof.isoformat() for c in cases)
    assert all(c["baseline_sigma_f"]>0 for c in cases)
    assert len({c["target_utc"] for c in cases})==len(cases)
    assert all(c["forecast_asof"]<c["target_utc"] for c in cases)


def test_index_nowcast_gates_synthetic_trend_improvements():
    conn,asof=populated()
    with conn:
        result=index_nowcast_decision(
            conn,CITY,asof,lead_minutes=30,config_version=CONFIG)
    assert result["history_hours"]>=72
    assert result["training_hours"]>=48
    assert result["validation_hours"]==24
    assert result["enabled"] is True
    assert result["status"]=="HISTORICAL_INDEX_HOLDOUT_IMPROVED_UNCERTIFIED"
    scores=result["validation"]
    assert scores["candidate_mae_f"]<scores["baseline_mae_f"]
    assert scores["candidate_crps"]<scores["baseline_crps"]
    assert scores["candidate_90_coverage"]>=.72
    mean,sigma=nowcast_prediction(53.,1.2,2.5,2.5,.5,result)
    assert mean>53. and math.isfinite(sigma) and sigma>0


def test_missing_history_or_delayed_receipt_is_not_training_data():
    conn,asof=populated(n=25)
    with conn:
        result=index_nowcast_decision(conn,CITY,asof,lead_minutes=30,
                                      config_version=CONFIG)
    assert result["enabled"] is False
    assert result["status"]=="INSUFFICIENT_INDEX_HOURS"
    assert result["history_hours"]<72
    conn2,asof2=populated(delayed=True)
    with conn2:
        cases=index_cases(conn2,CITY,asof2,lead_minutes=30,
                          config_version=CONFIG)
    assert cases==[]


def test_different_index_configuration_cannot_be_used_as_training_labels():
    conn,asof=populated()
    with conn:
        result=index_nowcast_decision(conn,CITY,asof,lead_minutes=30,
                                      config_version="chicago-temperature-v9.9")
    assert result["enabled"] is False
    assert result["history_hours"]==0


def test_future_publication_not_visible_in_historical_training_window():
    conn,asof=populated()
    earlier=asof-timedelta(hours=35)
    with conn:
        early=index_cases(conn,CITY,earlier,lead_minutes=30,
                          config_version=CONFIG)
        late=index_cases(conn,CITY,asof,lead_minutes=30,
                         config_version=CONFIG)
    assert len(early)<len(late)
    assert all(datetime.fromisoformat(c["target_utc"])<earlier for c in early)


def test_out_of_sample_degradation_prevents_deployment():
    conn,asof=populated()
    with conn:
        samples=index_cases(conn,CITY,asof,lead_minutes=30,
                            config_version=CONFIG)
    base=choose_index_nowcast(samples)
    assert base["enabled"] is True
    # Flip only later outcomes; the training selection and scale cannot see it.
    spoiled=[dict(x) for x in samples]
    for row in spoiled[-24:]:
        row["observed_index_f"]+=25.
    altered=choose_index_nowcast(spoiled)
    assert altered["model"]==base["model"]
    assert altered["sigma_scale"]==base["sigma_scale"]
    assert altered["enabled"] is False


def test_nonfinite_or_corrupted_index_rows_are_rejected():
    conn,asof=populated()
    conn.execute("UPDATE weather_hourly_index SET payload='{}' WHERE event_ms=("
                 "SELECT MIN(event_ms) FROM weather_hourly_index)")
    with conn:
        result=index_nowcast_decision(conn,CITY,asof,lead_minutes=30,
                                      config_version=CONFIG)
    assert result["enabled"] is False
    assert result["status"]=="INVALID_INDEX_EVIDENCE"


def test_out_of_range_lead_times_fail_closed():
    conn,asof=populated()
    with conn:
        result=index_nowcast_decision(conn,CITY,asof,lead_minutes=180,
                                      config_version=CONFIG)
    assert result["enabled"] is False
    assert result["status"]=="UNSUPPORTED_LEAD_TIME"


def test_hourly_forecast_exposes_point_in_time_nowcast_evidence():
    from kalshi_edge.weather.hourly import _forecast
    conn,asof=populated()
    with conn:
        result=_forecast(conn,CITY,asof)
    assert result["index_nowcast_status"] in (
        "INSUFFICIENT_INDEX_HOURS",
        "INDEX_HOLDOUT_NOT_IMPROVED",
        "HISTORICAL_INDEX_HOLDOUT_IMPROVED_UNCERTIFIED",
    )
    assert result["index_nowcast_lead_minutes"] in (5,15,30,45,60)
    assert result["index_nowcast_history_hours"]>=72
    assert result["calibration_certified"] is False
    assert result["real_orders_enabled"] is False


def test_hourly_dashboard_shows_historical_index_evidence_without_claiming_certification():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("forecast","")
    assert "Index nowcast evidence" in html
    assert "Historical index hours" in html
    assert "index_nowcast_validation" in html
    assert "Research only; not independently certified" in html
