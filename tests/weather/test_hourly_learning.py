"""Hourly WeatherEdge learning gates: point-in-time evidence and honest validation."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from kalshi_edge.weather.hourly import _init, _forecast
from kalshi_edge.weather.hourly_learning import (
    choose_correction, collect_verified_cases, apply_correction,
)

TARGET=datetime(2026,11,22,18,tzinfo=timezone.utc)
ASOF=TARGET-timedelta(minutes=40)
CITY="chicago"
EVENT="KXTEMPCHIHS-26NOV2213"
TICKER=EVENT+"-T54.99"


def cases(n: int, *, corrected: bool) -> list[dict]:
    result=[]
    for i in range(n):
        x=2.0*math.sin(i*1.91)
        current=55.0
        raw_mean=current+x
        # Candidate has learnable bias and trend error; control has zero-mean
        # structured noise, and should not win by introducing adjustments.
        error=(.9+.38*x) if corrected else .2*math.sin(i*.79)
        actual=raw_mean+error
        threshold=raw_mean+(.3 if i%2 else -.3)
        result.append({
            "target_utc":(TARGET+timedelta(hours=i)).isoformat(),
            "baseline_mean_f":raw_mean,"baseline_sigma_f":1.3,
            "current_index_f":current,"threshold_f":threshold,
            "observed_index_f":actual,"yes":int(actual>=threshold),
        })
    return result


def test_historical_learning_requires_enough_chronological_evidence():
    for n in (0, 20, 87):
        decision=choose_correction(cases(n,corrected=True))
        assert decision["enabled"] is False
        assert decision["status"]=="INSUFFICIENT_VERIFIED_HOURS"
        assert decision["verified_hours"]==n


def test_learned_bias_and_trend_need_to_beat_existing_model_on_held_out_hours():
    decision=choose_correction(cases(130,corrected=True))
    assert decision["enabled"] is True
    assert decision["status"]=="HISTORICAL_HOLDOUT_IMPROVED_NOT_CERTIFIED"
    assert decision["validation_hours"]>=24
    assert decision["training_hours"]>=64
    assert decision["validation"]["candidate_brier"]<decision["validation"]["baseline_brier"]
    assert decision["validation"]["candidate_mae_f"]<decision["validation"]["baseline_mae_f"]
    assert decision["validation"]["candidate_logloss"]<decision["validation"]["baseline_logloss"]
    new_mean,new_sigma=apply_correction(57,1.3,55,decision)
    assert new_mean>57 and new_sigma>0


def test_noisy_evidence_is_not_a_license_to_raise_confidence():
    decision=choose_correction(cases(130,corrected=False))
    assert decision["enabled"] is False
    assert decision["status"]=="HOLDOUT_DID_NOT_IMPROVE"
    assert apply_correction(57,1.3,55,decision)==(57,1.3)


def test_training_does_not_fit_on_heldout_outcomes():
    base=cases(120,corrected=True)
    perturbed=cases(120,corrected=True)
    for row in perturbed[-24:]:
        row["observed_index_f"]+=20.0
        row["yes"]=int(row["observed_index_f"]>=row["threshold_f"])
    a=choose_correction(base)
    b=choose_correction(perturbed)
    assert a["fitted_bias_f"]==b["fitted_bias_f"]
    assert a["fitted_trend_gain"]==b["fitted_trend_gain"]
    assert a["fitted_sigma_scale"]==b["fitted_sigma_scale"]
    assert a["enabled"] is True
    assert b["enabled"] is False


def seeded_verified_connection():
    conn=sqlite3.connect(":memory:")
    _init(conn)
    source_asof=ASOF.isoformat()
    predicted=54.9
    index={"city":CITY,"units":"fahrenheit","config_version":"chicago-v1",
           "timeseries":[{"t":int(TARGET.timestamp()*1000),"v":55.1,
                          "status":"normal","contributors":4}]}
    market={"ticker":TICKER,"event_ticker":EVENT,"status":"settled","result":"yes",
            "yes_sub_title":"55° or above","floor_strike":54.99,
            "rules_primary":"Kalshi Weather Index at 1pm ET",
            "settlement_ts":(TARGET+timedelta(minutes=7)).isoformat()}
    market_source={"markets":[market],"cursor":""}
    frozen={"city":CITY,"event_ticker":EVENT,"target_utc":TARGET.isoformat(),
            "source":"KALSHI_WEATHER_INDEX","status":"READY_RESEARCH",
            "asof":source_asof,"predicted_index_f":predicted,
            "sigma_f":1.3,"current_index_f":54.8,
            "source_event_ms":int((ASOF-timedelta(minutes=5)).timestamp()*1000),
            "source_received_at":(ASOF-timedelta(minutes=4)).isoformat(),
            "selected_market":{"ticker":TICKER,"threshold_f":55.,
                               "probability_yes":.47}}
    raw=json.dumps(frozen,sort_keys=True,allow_nan=False)
    index_raw=json.dumps(index,sort_keys=True)
    market_raw=json.dumps(market_source,sort_keys=True)
    conn.execute("""INSERT INTO weather_hourly_snapshots
    (city,event_ticker,market_ticker,asof,target_utc,source_reading_ms,
     source_receipt,forecast_json,forecast_sha) VALUES(?,?,?,?,?,?,?,?,?)""",
    (CITY,EVENT,TICKER,source_asof,TARGET.isoformat(),frozen["source_event_ms"],
     frozen["source_received_at"],raw,hashlib.sha256(raw.encode()).hexdigest()))
    conn.execute("""INSERT INTO weather_hourly_settlements
    (city,event_ticker,market_ticker,result,observed_index_f,observed_event_ms,
     checked_at,source_index_sha,market_sha,source_index_raw,market_raw)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
    (CITY,EVENT,TICKER,"yes",55.1,int(TARGET.timestamp()*1000),
     (TARGET+timedelta(minutes=7)).isoformat(),
     hashlib.sha256(index_raw.encode()).hexdigest(),
     hashlib.sha256(market_raw.encode()).hexdigest(),index_raw,market_raw))
    conn.commit()
    return conn


def test_only_actual_published_verified_outcomes_enter_training():
    with seeded_verified_connection() as conn:
        early,status=collect_verified_cases(conn,CITY,TARGET+timedelta(minutes=5))
        assert early==[] and status is None
        cases_now,status=collect_verified_cases(conn,CITY,TARGET+timedelta(minutes=10))
        assert status is None
        assert len(cases_now)==1
        case=cases_now[0]
        assert case["baseline_mean_f"]==54.9
        assert case["observed_index_f"]==55.1
        assert case["yes"]==1


def test_tampered_evidence_fails_closed_and_never_trains():
    with seeded_verified_connection() as conn:
        conn.execute("UPDATE weather_hourly_snapshots SET forecast_json='{}'")
        cases_now,status=collect_verified_cases(conn,CITY,TARGET+timedelta(minutes=10))
        assert cases_now==[]
        assert status=="INVALID_VERIFIED_EVIDENCE"


def test_future_data_does_not_leak_into_hourly_forecast():
    with seeded_verified_connection() as conn:
        conn.execute("""INSERT INTO weather_hourly_index
          (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
          VALUES(?,?,?,?,?,?,?,?)""",
          (CITY,int((ASOF-timedelta(minutes=5)).timestamp()*1000),
           (ASOF-timedelta(minutes=4)).isoformat(),54.8,"normal","v1","sha","{}"))
        result=_forecast(conn,CITY,ASOF)
        assert result["learning_verified_hours"]==0
        assert result["learning_status"]=="INSUFFICIENT_VERIFIED_HOURS"
        assert result["model_kind"]=="UNVALIDATED_HOURLY_INDEX_TREND"
        assert result["calibration_certified"] is False
