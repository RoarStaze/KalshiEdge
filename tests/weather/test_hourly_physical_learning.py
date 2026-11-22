"""V4 fixed prospective ensemble: deterministic tests are not skill claims."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.weather.hourly_physical_learning import (
    _init, lead_bucket, freeze_pair, collect_cases,
    select_candidate, evaluate_lockbox, apply_locked_blend,
)
from kalshi_edge.weather.hourly import _init as init_hourly

UTC=timezone.utc
START=datetime(2026,10,1,12,tzinfo=UTC)
CONFIG="chicago-temperature-v1.1"


def synthetic(n=120, *, better=True, verified=True):
    rows=[]
    for i in range(n):
        actual=57+2*math.sin(.27*i)+.03*i
        base=actual+(1.9 if better else .07*math.sin(i*.83))
        hrrr=actual+.11*math.sin(i*.53) if better else actual+2.5
        nbm=actual-.13*math.sin(i*.71) if better else actual-2.3
        threshold=actual+(.6 if i%2 else -.6)
        rows.append({
            "target_utc":(START+timedelta(hours=i)).isoformat(),
            "baseline_f":base,"sigma_f":1.3,"hrrr_f":hrrr,"nbm_f":nbm,
            "observed_f":actual,"threshold_f":threshold,
            "market_verified":verified,
            "config_version":CONFIG,
        })
    return rows


def test_index_lead_bucket_is_explicit_and_bounded():
    assert lead_bucket(29.1)==30
    assert lead_bucket(60)==60
    assert lead_bucket(6)==5
    assert lead_bucket(85) is None
    assert lead_bucket(-1) is None


def test_candidate_chooses_trained_model_that_beats_existing_holdout():
    decision=select_candidate(synthetic(88))
    assert decision["status"]=="HISTORICAL_POINT_HOLDOUT_IMPROVED"
    assert decision["candidate_ready"] is True
    assert decision["train_hours"]==64
    assert decision["validation_hours"]==24
    assert decision["validation"]["candidate_mae_f"]<decision["validation"]["baseline_mae_f"]
    assert decision["validation"]["candidate_crps"]<decision["validation"]["baseline_crps"]
    assert sum(decision["weights"])==pytest.approx(1.)
    assert all(0<=w<=1 for w in decision["weights"])


def test_noisy_or_insufficient_evidence_does_not_lock_any_model():
    assert select_candidate(synthetic(87))["status"]=="INSUFFICIENT_PAIRED_HOURS"
    bad=select_candidate(synthetic(100,better=False))
    assert bad["candidate_ready"] is False


def test_candidate_model_is_selected_without_peeking_at_holdout_labels():
    rows=synthetic(88)
    other=[dict(r) for r in rows]
    for row in other[-24:]:
        row["observed_f"]+=30
        row["market_verified"]=False
    a=select_candidate(rows)
    b=select_candidate(other)
    assert a["weights"]==b["weights"]
    assert a["bias_f"]==b["bias_f"]
    assert a["sigma_scale"]==b["sigma_scale"]
    assert b["candidate_ready"] is False


def test_fixed_prospective_lockbox_needs_genuine_settlement_reconciliation():
    decision=select_candidate(synthetic(88))
    lock={"locked_at":synthetic(88)[-1]["target_utc"],**decision}
    future=synthetic(120)[88:]
    accepted=evaluate_lockbox(lock,future)
    assert accepted["enabled"] is True
    assert accepted["status"]=="PROSPECTIVE_LOCKBOX_IMPROVED_NOT_CERTIFIED"
    assert accepted["lockbox_hours"]==32
    assert accepted["market_verified_hours"]==32
    assert accepted["metrics"]["candidate_brier"]<accepted["metrics"]["baseline_brier"]
    assert accepted["metrics"]["candidate_logloss"]<accepted["metrics"]["baseline_logloss"]
    assert accepted["metrics"]["candidate_90_coverage"]>=.75
    assert apply_locked_blend(60,1.3,59.5,59.6,accepted) != (60,1.3)
    no_markets=[{**r,"market_verified":False} for r in future]
    rejected=evaluate_lockbox(lock,no_markets)
    assert rejected["enabled"] is False
    assert rejected["market_verified_hours"]==0
    assert apply_locked_blend(60,1.3,59.5,59.6,rejected)==(60,1.3)


def test_prospective_lockbox_is_first_32_after_lock_not_opportunistic_latest_32():
    lock={"locked_at":synthetic(88)[-1]["target_utc"],**select_candidate(synthetic(88))}
    future=synthetic(152)[88:]
    broken=[dict(r) for r in future]
    for row in broken[:32]:
        row["observed_f"]+=20
    score=evaluate_lockbox(lock,broken)
    assert score["enabled"] is False
    assert score["lockbox_hours"]==32
    # Adding good later outcomes must not permit re-testing a new window.
    assert evaluate_lockbox(lock,broken+synthetic(200)[152:])["enabled"] is False


def test_freeze_pair_does_not_accept_late_noaa_or_mismatched_index_config():
    conn=sqlite3.connect(":memory:")
    init_hourly(conn)
    _init(conn)
    asof=START-timedelta(minutes=30)
    prediction={
        "city":"chicago","asof":asof.isoformat(),
        "target_utc":START.isoformat(),"source":"KALSHI_WEATHER_INDEX",
        "status":"NO_VERIFIED_HOURLY_MARKETS",
        "source_config_version":CONFIG,
        "source_event_ms":int((asof-timedelta(minutes=4)).timestamp()*1000),
        "source_received_at":(asof-timedelta(minutes=2)).isoformat(),
        "pre_physical_mean_f":60.0,"pre_physical_sigma_f":1.4,
        "markets":[],
        "physical_ensemble":{
            "status":"RESEARCH_ONLY_NOT_PROMOTED",
            "calibration_version":CONFIG,
            "hrrr_index_f":59.2,"nbm_index_f":59.7,
            "model_receipts":{"HRRR":(asof-timedelta(minutes=3)).isoformat(),
                              "NBM":(asof-timedelta(minutes=1)).isoformat()},
            "model_runs":{"HRRR":(asof-timedelta(hours=2)).isoformat(),
                          "NBM":(asof-timedelta(hours=2)).isoformat()},
        },
    }
    assert freeze_pair(conn,prediction,asof) is True
    assert freeze_pair(conn,prediction,asof) is False  # immutable once
    assert conn.execute("SELECT COUNT(*) FROM weather_hourly_physical_pairs").fetchone()[0]==1
    late=json.loads(json.dumps(prediction))
    late["target_utc"]=(START+timedelta(hours=1)).isoformat()
    late["physical_ensemble"]["model_receipts"]["HRRR"]=(asof+timedelta(seconds=2)).isoformat()
    assert freeze_pair(conn,late,asof) is False
    invalid=json.loads(json.dumps(prediction))
    invalid["target_utc"]=(START+timedelta(hours=1)).isoformat()
    invalid["physical_ensemble"]["calibration_version"]="other-version"
    assert freeze_pair(conn,invalid,asof) is False


def test_no_exact_index_observation_is_never_fabricated_as_label():
    conn=sqlite3.connect(":memory:")
    init_hourly(conn)
    _init(conn)
    asof=START-timedelta(minutes=30)
    prediction={
        "city":"chicago","asof":asof.isoformat(),
        "target_utc":START.isoformat(),"source":"KALSHI_WEATHER_INDEX",
        "source_config_version":CONFIG,
        "source_event_ms":int((asof-timedelta(minutes=4)).timestamp()*1000),
        "source_received_at":(asof-timedelta(minutes=2)).isoformat(),
        "pre_physical_mean_f":60.0,"pre_physical_sigma_f":1.4,
        "markets":[],
        "physical_ensemble":{
            "status":"RESEARCH_ONLY_NOT_PROMOTED",
            "calibration_version":CONFIG,"hrrr_index_f":59.2,"nbm_index_f":59.7,
            "model_receipts":{"HRRR":(asof-timedelta(minutes=3)).isoformat(),
                              "NBM":(asof-timedelta(minutes=1)).isoformat()},
            "model_runs":{"HRRR":(asof-timedelta(hours=2)).isoformat(),
                          "NBM":(asof-timedelta(hours=2)).isoformat()}}}
    assert freeze_pair(conn,prediction,asof)
    assert collect_cases(conn,"chicago",CONFIG,30,START+timedelta(minutes=10))==[]
    def reading(at,received,temperature):
        raw={"t":int(at.timestamp()*1000),"v":temperature,"status":"normal","contributors":4}
        conn.execute("""INSERT OR IGNORE INTO weather_hourly_index
          (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
          VALUES(?,?,?,?,?,?,?,?)""",
          ("chicago",raw["t"],received.isoformat(),temperature,"normal",CONFIG,
           "a"*64,json.dumps(raw,sort_keys=True)))
    reading(START-timedelta(minutes=1),START+timedelta(minutes=1),60.)
    assert collect_cases(conn,"chicago",CONFIG,30,START+timedelta(minutes=10))==[]
    reading(START,START+timedelta(minutes=12),60.)
    assert collect_cases(conn,"chicago",CONFIG,30,START+timedelta(minutes=15))==[]
    conn.execute("DELETE FROM weather_hourly_index WHERE event_ms=?",(int(START.timestamp()*1000),))
    reading(START,START+timedelta(minutes=2),60.)
    cases=collect_cases(conn,"chicago",CONFIG,30,START+timedelta(minutes=15))
    assert len(cases)==1
    assert cases[0]["observed_f"]==60.0
    conn.execute("UPDATE weather_hourly_physical_pairs SET forecast_json='{}'")
    assert collect_cases(conn,"chicago",CONFIG,30,START+timedelta(minutes=15))==[]


def test_live_read_path_rejects_tampered_locked_candidate():
    from kalshi_edge.weather.hourly_physical_learning import physical_live_decision
    conn=sqlite3.connect(":memory:")
    init_hourly(conn)
    _init(conn)
    asof=START-timedelta(minutes=30)
    conn.execute("""INSERT INTO weather_hourly_physical_locks
         (city,config_version,lead_minutes,locked_at,params_json,params_sha)
         VALUES(?,?,?,?,?,?)""",
         ("chicago",CONFIG,30,(asof-timedelta(days=1)).isoformat(),
          json.dumps({"candidate_ready":True}),"0"*64))
    actual={"status":"RESEARCH_ONLY_NOT_PROMOTED","calibration_version":CONFIG,
            "hrrr_index_f":55.,"nbm_index_f":57.}
    result=physical_live_decision(conn,"chicago",CONFIG,START,asof,actual)
    assert result["status"]=="TAMPERED_PHYSICAL_MODEL_LOCK"
    assert result["enabled"] is False


def test_hourly_weather_ui_exposes_prospective_evidence_without_promising_accuracy():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("forecast","")
    assert "Physical ensemble validation" in html
    assert "Paired hourly forecast evidence" in html
    assert "Prospective lockbox hours" in html
    assert "Settlement-verified lockbox hours" in html
    assert "physical_adaptive_status" in html
    assert "not a profit guarantee" in html


def test_locked_coefficients_and_forecasts_do_not_enable_real_weather_orders():
    from kalshi_edge.weather.hourly import _forecast
    conn=sqlite3.connect(":memory:")
    init_hourly(conn)
    _init(conn)
    result=_forecast(conn,"chicago",START-timedelta(minutes=30))
    assert result["physical_adaptive_status"]=="WAITING_FOR_MATCHED_EVIDENCE"
    assert result["real_orders_enabled"] is False
    assert result["calibration_certified"] is False


def test_physical_training_target_is_next_index_hour_even_if_market_lists_later_hour():
    from kalshi_edge.weather.hourly import _forecast
    conn=sqlite3.connect(":memory:")
    init_hourly(conn)
    _init(conn)
    now=START-timedelta(minutes=30)
    last=now-timedelta(minutes=2)
    raw={"t":int(last.timestamp()*1000),"v":59.0,
         "status":"normal","contributors":3}
    conn.execute("""INSERT INTO weather_hourly_index
         (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
         VALUES(?,?,?,?,?,?,?,?)""",
         ("chicago",raw["t"],(last+timedelta(minutes=1)).isoformat(),
          59.0,"normal",CONFIG,"a"*64,json.dumps(raw)))
    far=START+timedelta(hours=2)
    conn.execute("""INSERT INTO weather_hourly_markets
          (ticker,city,event_ticker,target_utc,threshold_f,received_at,metadata)
          VALUES(?,?,?,?,?,?,?)""",
          ("LATER","chicago","KXTEMPCHIHS-LATER",far.isoformat(),60.0,
           (now-timedelta(minutes=1)).isoformat(),"{}"))
    normal=_forecast(conn,"chicago",now)
    physical=_forecast(conn,"chicago",now,force_next_index_hour=True)
    assert normal["target_utc"]==far.isoformat()
    assert physical["target_utc"]==START.isoformat()
