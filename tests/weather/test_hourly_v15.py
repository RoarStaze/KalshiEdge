"""WeatherEdge V15: leak-safe NOAA fusion promotion; all numeric cases synthetic."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.weather.hourly_v15 import (
    V15_KIND, fit_v15, predict_v15, select_v15,
    evaluate_v15_pairs, v15_live_decision,
)

UTC=timezone.utc
START=datetime(2026,11,1,tzinfo=UTC)
V14="HISTORICAL_REGIME_HYBRID_INDEX_NOWCAST_V14"


def paired(n=120, *, edge=True):
    result=[]
    for i in range(n):
        base=50+3*math.sin(i*.21)
        signal=1.4*math.sin(i*.43)+.8*math.cos(i*.11)
        observed=base+(signal if edge else .04*math.sin(i*1.1))
        hrrr=base+signal+.13*math.sin(i*.27) if edge else base+3.
        nbm=base+signal-.17*math.sin(i*.33) if edge else base-3.
        result.append({
            "target_utc":(START+timedelta(hours=i)).isoformat(),
            "baseline_f":base,"sigma_f":1.4,
            "hrrr_f":hrrr,"nbm_f":nbm,
            "observed_f":observed,"baseline_kind":V14,
            "config_version":"synthetic-current-config",
            "market_verified":False,"threshold_f":None})
    return result


def test_v15_fit_improves_synthetic_out_of_sample_noaa_edge():
    rows=paired()
    params=fit_v15(rows[:72])
    mean,sigma=predict_v15(rows[90],params)
    assert math.isfinite(mean) and sigma>0
    result=select_v15(rows[:96])
    assert result["status"]=="V15_HOLDOUT_IMPROVED",result
    assert result["promotable"] is True
    assert result["incumbent"]["mae_f"]>result["candidate"]["mae_f"]
    assert result["candidate"]["crps"]<result["incumbent"]["crps"]
    assert result["baseline_kind"]==V14


def test_v15_training_does_not_access_future_validation_outcomes():
    rows=paired(96)
    first=select_v15(rows)
    altered=copy.deepcopy(rows)
    for row in altered[-24:]:
        row["observed_f"]+=40.
    second=select_v15(altered)
    assert first["parameters"]==second["parameters"]
    assert second["promotable"] is False


def test_v15_rejects_unsupported_malformed_and_insufficient_evidence():
    assert select_v15(paired(87))["status"]=="INSUFFICIENT_PAIRED_HOURS"
    no_edge=select_v15(paired(96,edge=False))
    assert no_edge["promotable"] is False
    rows=paired(100)
    rows[-1]["baseline_kind"]="HISTORICAL_ARCHIVE_HYBRID_INDEX_NOWCAST_V13"
    mixed=select_v15(rows)
    assert mixed["status"]=="INSUFFICIENT_PAIRED_HOURS"
    with pytest.raises(ValueError):
        predict_v15({**paired(1)[0],"hrrr_f":float("nan")},fit_v15(paired(88)))


def test_v15_prospective_score_requires_immutable_snapshot_window():
    rows=paired(120)
    scores=evaluate_v15_pairs(rows)
    assert scores["evaluated_hours"]>=24
    assert scores["status"]=="V15_RESEARCH_SCORED"
    assert scores["market_verified_hours"]==0
    assert scores["brier"] is None
    assert scores["calibration_certified"] is False
    assert scores["real_orders_enabled"] is False


def test_v15_live_requires_matching_provenance_and_exact_new_noaa_snapshot(monkeypatch):
    from kalshi_edge.weather import hourly_v15 as v
    rows=paired(96)
    monkeypatch.setattr(v,"collect_cases",lambda *args,**kwargs:rows)
    decision=v15_live_decision(
        object(),"chicago","synthetic-current-config",
        START+timedelta(days=4),START+timedelta(days=3,hours=23,minutes=30),
        30,{"status":"RESEARCH_ONLY_NOT_PROMOTED",
            "calibration_version":"synthetic-current-config",
            "forecast_target_utc":(START+timedelta(days=4)).isoformat(),
            "hrrr_index_f":55.,"nbm_index_f":54.,
            "model_receipts":{
                "HRRR":(START+timedelta(days=3,hours=23,minutes=20)).isoformat(),
                "NBM":(START+timedelta(days=3,hours=23,minutes=25)).isoformat()},
            "model_runs":{
                "HRRR":(START+timedelta(days=3,hours=21)).isoformat(),
                "NBM":(START+timedelta(days=3,hours=21)).isoformat()},
        },baseline_kind=V14)
    assert decision["enabled"] is True,decision
    assert decision["model_kind"]==V15_KIND
    assert decision["market_verified_hours"]==0
    bad=copy.deepcopy(decision["physics"])
    bad["model_receipts"]["NBM"]=(START+timedelta(days=4)).isoformat()
    assert not v15_live_decision(
        object(),"chicago","synthetic-current-config",
        START+timedelta(days=4),START+timedelta(days=3,hours=23,minutes=30),
        30,bad,baseline_kind=V14)["enabled"]


def test_v15_live_rejects_new_config_or_wrong_baseline(monkeypatch):
    from kalshi_edge.weather import hourly_v15 as v
    monkeypatch.setattr(v,"collect_cases",lambda *args,**kwargs:paired(96))
    now=START+timedelta(days=3,hours=23,minutes=30)
    target=START+timedelta(days=4)
    physics={"status":"RESEARCH_ONLY_NOT_PROMOTED",
             "calibration_version":"different-config",
             "forecast_target_utc":target.isoformat(),
             "hrrr_index_f":55.,"nbm_index_f":54.,
             "model_receipts":{"HRRR":(now-timedelta(minutes=2)).isoformat(),
                               "NBM":(now-timedelta(minutes=1)).isoformat()},
             "model_runs":{"HRRR":(now-timedelta(hours=2)).isoformat(),
                           "NBM":(now-timedelta(hours=2)).isoformat()}}
    assert not v15_live_decision(object(),"chicago","synthetic-current-config",
                                  target,now,30,physics,baseline_kind=V14)["enabled"]


def test_v15_forecast_promotes_new_model_identity_without_order_or_market_fabrication(monkeypatch):
    import json
    import sqlite3
    from kalshi_edge.weather import hourly
    from kalshi_edge.weather.hourly import _init as init_hourly, _forecast
    from kalshi_edge.weather.hourly_v15 import V15_KIND
    conn=sqlite3.connect(":memory:")
    init_hourly(conn)
    now=START+timedelta(hours=20,minutes=30)
    at=now-timedelta(minutes=3)
    raw={"t":int(at.timestamp()*1000),"v":59.0,"status":"normal","contributors":3}
    conn.execute("""INSERT INTO weather_hourly_index
        (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
        VALUES(?,?,?,?,?,?,?,?)""",
        ("chicago",raw["t"],(at+timedelta(minutes=1)).isoformat(),59.,
         "normal","synthetic-current-config","a"*64,json.dumps(raw)))
    monkeypatch.setattr(hourly,"read_hourly_physics",lambda *args,**kwargs:{
        "status":"RESEARCH_ONLY_NOT_PROMOTED","calibration_version":"synthetic-current-config",
        "hrrr_index_f":61.,"nbm_index_f":61.})
    monkeypatch.setattr(hourly,"v15_live_decision",lambda *args,**kwargs:{
        "enabled":True,"status":"V15_PAIRED_TEMPERATURE_IMPROVED",
        "model_kind":V15_KIND,"market_verified_hours":0,
        "parameters":{"nwp_gain":.8,"contrast_gain":0.,"bias_f":0.,
                      "sigma_scale":1.,"spread_gain":0.},
        "evidence":{"incumbent":{"mae_f":1.},"candidate":{"mae_f":.5}},
        "history_hours":100,"train_hours":75,"validation_hours":24,
    })
    monkeypatch.setattr(hourly,"physical_live_decision",lambda *args,**kwargs:{
        "enabled":False,"status":"COLLECTING_PAIRED_HOURS"})
    output=_forecast(conn,"chicago",now)
    assert output["model_kind"]==V15_KIND
    assert output["pre_physical_model_kind"]==V15_KIND
    assert output["pre_physical_mean_f"]==output["predicted_index_f"]
    assert output["predicted_index_f"]>output["current_index_f"]
    assert output["real_orders_enabled"] is False
    assert output["calibration_certified"] is False
    conn.close()


def test_v15_readonly_report_without_local_database(tmp_path):
    from kalshi_edge.weather.hourly_v15 import compare_local_v15
    result=compare_local_v15(tmp_path/"missing.sqlite3")
    assert result["status"]=="NO_LOCAL_WEATHER_HISTORY"
    assert result["groups"]==[]
    assert result["real_orders_enabled"] is False


def test_after_v15_activation_frozen_pair_keeps_original_v14_counterfactual():
    import json
    import sqlite3
    from kalshi_edge.weather.hourly_physical_learning import freeze_pair, _init
    from kalshi_edge.weather.hourly_v15 import V15_KIND
    conn=sqlite3.connect(":memory:")
    _init(conn)
    now=START+timedelta(hours=20,minutes=30)
    target=START+timedelta(hours=21)
    row={
        "city":"chicago","source":"KALSHI_WEATHER_INDEX","asof":now.isoformat(),
        "target_utc":target.isoformat(),"source_config_version":"test-config",
        "source_event_ms":int((now-timedelta(minutes=3)).timestamp()*1000),
        "source_received_at":(now-timedelta(minutes=1)).isoformat(),
        "model_kind":V15_KIND,
        "pre_physical_mean_f":61.,"pre_physical_sigma_f":1.2,
        "pre_physical_model_kind":V15_KIND,
        "v15_reference_mean_f":59.4,"v15_reference_sigma_f":1.5,
        "v15_reference_model_kind":V14,
        "physical_ensemble":{
            "status":"RESEARCH_ONLY_NOT_PROMOTED",
            "calibration_version":"test-config",
            "hrrr_index_f":60.5,"nbm_index_f":60.9,
            "model_receipts":{"HRRR":(now-timedelta(minutes=5)).isoformat(),
                              "NBM":(now-timedelta(minutes=3)).isoformat()},
            "model_runs":{"HRRR":(now-timedelta(hours=2)).isoformat(),
                          "NBM":(now-timedelta(hours=2)).isoformat()},
        },
        "markets":[],
    }
    assert freeze_pair(conn,row,now)
    raw=conn.execute("SELECT frozen_json FROM weather_hourly_physical_pairs").fetchone()[0]
    pair=json.loads(raw)
    assert pair["baseline_kind"]==V14
    assert pair["baseline_f"]==59.4
    assert pair["sigma_f"]==1.5
    assert pair["baseline_kind"]!=V15_KIND


def test_v15_never_overrides_preexisting_prospective_market_verified_physical_lock(monkeypatch):
    import json
    import sqlite3
    from kalshi_edge.weather import hourly
    conn=sqlite3.connect(":memory:")
    hourly._init(conn)
    now=START+timedelta(hours=20,minutes=30)
    at=now-timedelta(minutes=3)
    raw={"t":int(at.timestamp()*1000),"v":59.0,"status":"normal","contributors":4}
    conn.execute("""INSERT INTO weather_hourly_index
        (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
        VALUES(?,?,?,?,?,?,?,?)""",("chicago",raw["t"],
          (at+timedelta(minutes=1)).isoformat(),59.,
          "normal","synthetic-current-config","a"*64,json.dumps(raw)))
    monkeypatch.setattr(hourly,"read_hourly_physics",lambda *args,**kwargs:{
        "status":"RESEARCH_ONLY_NOT_PROMOTED","calibration_version":"synthetic-current-config",
        "hrrr_index_f":61.,"nbm_index_f":61.})
    monkeypatch.setattr(hourly,"v15_live_decision",lambda *args,**kwargs:{
        "enabled":True,"parameters":{"nwp_gain":.8,"contrast_gain":0.,
        "bias_f":0.,"sigma_scale":1.,"spread_gain":0.},
        "history_hours":100,"train_hours":75,"validation_hours":24,
        "status":"V15_PAIRED_TEMPERATURE_IMPROVED","market_verified_hours":0})
    monkeypatch.setattr(hourly,"physical_live_decision",lambda *args,**kwargs:{
        "enabled":True,"status":"PROSPECTIVE_LOCKBOX_IMPROVED_NOT_CERTIFIED",
        "weights":[0.,.5,.5],"bias_f":0.,"sigma_scale":1.,
        "paired_hours":125,"lockbox_hours":32,
        "market_verified_hours":32})
    result=hourly._forecast(conn,"chicago",now)
    assert result["model_kind"]=="PROSPECTIVE_LOCKBOX_PHYSICAL_INDEX_V4"
    assert result["v15_status"]=="MARKET_VERIFIED_PHYSICAL_LOCK_PRIORITY"
    assert result["real_orders_enabled"] is False
    conn.close()
