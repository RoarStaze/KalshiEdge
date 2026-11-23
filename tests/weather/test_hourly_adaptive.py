"""Adaptive hourly Index nowcaster tests. Synthetic fit is not a skill claim."""
from __future__ import annotations

import math

import pytest

from kalshi_edge.weather.hourly_adaptive import (
    fit_adaptive, predict_adaptive, validate_adaptive,
)
from kalshi_edge.weather.hourly_nowcast import (
    choose_index_nowcast, nowcast_prediction,
)


def samples(count=108):
    """Synthetic temperature trajectories where static weights miss diurnal drift."""
    rows=[]
    for i in range(count):
        fast=2.8*math.sin(i*.47)
        slow=2.4*math.cos(i*.31)
        hours=.35+.38*((i%7)/6)
        current=46.+4*math.sin(i*.11)
        actual=current+hours*(.13*fast+.61*slow+.75)+.035*math.sin(i*1.83)
        baseline=current+.55*fast*hours
        rows.append({
            "target_utc":f"2026-10-{(i//24)+1:02d}T{i%24:02d}:00:00+00:00",
            "current_index_f":current,
            "fast_slope":fast,"slow_slope":slow,
            "hours_ahead":hours,"observed_index_f":actual,
            "baseline_mean_f":baseline,"baseline_sigma_f":.74,
        })
    return rows


def test_robust_regularized_model_learns_from_training_only():
    trained=fit_adaptive(samples(72))
    assert trained["fast_gain"]==pytest.approx(.13,abs=.12)
    assert trained["slow_gain"]==pytest.approx(.61,abs=.12)
    assert trained["bias_per_hour"]==pytest.approx(.75,abs=.25)
    assert .75<=trained["sigma_scale"]<=1.65
    assert validate_adaptive(trained)
    mean,sigma=predict_adaptive(
        50.,.74,2.0,-1.5,.5,trained)
    assert mean==pytest.approx(50.+.5*(.13*2-.61*1.5+.75),abs=.25)
    assert sigma>0


def test_one_extreme_outlier_does_not_destroy_model():
    normal=fit_adaptive(samples(72))
    contaminated=samples(72)
    contaminated[14]["observed_index_f"]+=70
    robust=fit_adaptive(contaminated)
    assert validate_adaptive(robust)
    assert abs(robust["fast_gain"]-normal["fast_gain"])<.22
    assert abs(robust["slow_gain"]-normal["slow_gain"])<.22
    assert abs(robust["bias_per_hour"]-normal["bias_per_hour"])<.3


def test_perfectly_collinear_inputs_have_finite_bounded_solution():
    rows=samples(72)
    for row in rows:
        row["slow_slope"]=row["fast_slope"]
    p=fit_adaptive(rows)
    assert validate_adaptive(p)
    assert sum([p["fast_gain"],p["slow_gain"]])<=1.5
    assert all(math.isfinite(p[key]) for key in ("fast_gain","slow_gain","bias_per_hour"))


@pytest.mark.parametrize("coeff",[
    {"fast_gain":-99.0,"slow_gain":.4,"bias_per_hour":0,"sigma_scale":1.},
    {"fast_gain":.3,"slow_gain":.4,"bias_per_hour":float("nan"),"sigma_scale":1.},
    {"fast_gain":.3,"slow_gain":.4,"bias_per_hour":0,"sigma_scale":-1.},
    {"fast_gain":1.,"slow_gain":1.,"bias_per_hour":0,"sigma_scale":1.},
])
def test_invalid_coefficients_never_control_live_prediction(coeff):
    assert not validate_adaptive(coeff)
    assert predict_adaptive(51,1.2,2.,3.,.5,coeff)==(51,1.2)


def test_actual_nowcast_selection_can_choose_robust_adaptive_model():
    choice=choose_index_nowcast(samples(104))
    assert choice["enabled"] is True
    assert choice["model"]=="OBSERVATION_ADAPTIVE_V3"
    assert choice["parameters"] is not None
    assert validate_adaptive(choice["parameters"])
    assert choice["validation"]["candidate_mae_f"]<choice["validation"]["baseline_mae_f"]
    assert choice["validation"]["candidate_crps"]<choice["validation"]["baseline_crps"]
    before=50.
    after,sigma=nowcast_prediction(before,1.2,2.,-1.,.5,choice)
    expected,expected_sigma=predict_adaptive(before,1.2,2.,-1.,.5,
                                               choice["parameters"])
    assert after==pytest.approx(expected)
    assert sigma==pytest.approx(expected_sigma)


def test_changing_holdout_outcomes_never_refits_learned_nowcast():
    original=samples(104)
    clean=choose_index_nowcast(original)
    damaged=[dict(row) for row in original]
    for row in damaged[-24:]:
        row["observed_index_f"]+=30.
    after=choose_index_nowcast(damaged)
    assert clean["model"]==after["model"]
    assert clean["parameters"]==after["parameters"]
    assert clean["sigma_scale"]==after["sigma_scale"]
    assert after["enabled"] is False


def test_adaptive_model_never_activates_without_old_training_hours():
    r=choose_index_nowcast(samples(45))
    assert r["enabled"] is False
    assert r["status"]=="INSUFFICIENT_INDEX_HOURS"


def test_weather_forecast_technical_panel_shows_chosen_actual_nowcast_model():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("forecast","")
    assert "Selected hourly prediction model" in html
    assert "index_nowcast_selected_model" in html
    assert "Not a verified accuracy claim" in html
    assert "Research only; not independently certified" in html


def test_real_hourly_forecast_version_marks_adaptive_model_for_downstream_validation(monkeypatch):
    import json
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from kalshi_edge.weather import hourly
    now=datetime(2026,10,5,17,30,tzinfo=timezone.utc)
    observed=now-timedelta(minutes=2)
    raw={"t":int(observed.timestamp()*1000),
         "v":52.,"status":"normal","contributors":4}
    conn=sqlite3.connect(":memory:")
    hourly._init(conn)
    conn.execute("""INSERT INTO weather_hourly_index
      (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
      VALUES(?,?,?,?,?,?,?,?)""",(
        "chicago",raw["t"],(observed+timedelta(minutes=1)).isoformat(),
        52.,"normal","chicago-temperature-v1.1","a"*64,json.dumps(raw)))
    fitted=fit_adaptive(samples(80))
    monkeypatch.setattr(hourly,"index_nowcast_decision",
        lambda *args,**kwargs:{
            "enabled":True,"status":"HISTORICAL_INDEX_HOLDOUT_IMPROVED_UNCERTIFIED",
            "history_hours":80,"validation_hours":24,
            "model":"OBSERVATION_ADAPTIVE_V3",
            "parameters":fitted,"live_slopes":(1.5,-.5,52.,raw["t"]),
            "validation":None})
    result=hourly._forecast(conn,"chicago",now)
    assert result["model_kind"]=="HISTORICAL_ADAPTIVE_INDEX_NOWCAST_V3"
    assert result["index_nowcast_selected_model"]=="OBSERVATION_ADAPTIVE_V3"
    assert result["real_orders_enabled"] is False
    assert result["calibration_certified"] is False
