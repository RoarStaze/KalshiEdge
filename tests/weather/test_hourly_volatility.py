"""V8 regime-sensitive Kalshi Index uncertainty, not demonstrated forecast skill."""
from __future__ import annotations

import math

import pytest

from kalshi_edge.weather.hourly_volatility import (
    fit_volatility, predict_volatility, validate_volatility,
)
from kalshi_edge.weather.hourly_nowcast import (
    VOLATILITY_MODEL, choose_index_nowcast, nowcast_prediction,
)


def cases(n=160):
    """A deterministic illustrative heteroscedastic process; never actual weather."""
    result = []
    for i in range(n):
        fast = 1.25 * math.sin(i * .29)
        slow = fast + (3.5 if i % 2 else .2)
        hours = .5
        current = 50 + 2 * math.sin(i * .09)
        ideal = current + hours * (.15 * fast + .55 * slow + .7)
        shock = (2.2 if i % 2 else .35) * math.sin(i * 1.9)
        actual = ideal + shock
        result.append({
            "target_utc": f"2026-10-{1 + i//24:02d}T{i%24:02d}:00:00+00:00",
            "current_index_f": current, "fast_slope": fast,
            "slow_slope": slow, "hours_ahead": hours,
            "observed_index_f": actual,
            "baseline_mean_f": current + .55 * fast * hours,
            "baseline_sigma_f": .8,
        })
    return result


def test_volatility_fit_is_train_only_bounded_and_sigma_reflects_disagreement():
    data = cases()
    fitted = fit_volatility(data[:-24])
    assert validate_volatility(fitted)
    assert 0 < fitted["volatility_gain"] <= 1.25
    quiet_mean, quiet_sigma = predict_volatility(55., 1., 1., 1., .5, fitted)
    volatile_mean, volatile_sigma = predict_volatility(55., 1., 1., 4., .5, fitted)
    assert quiet_mean != 55.  # adaptive mean is still used
    assert volatile_sigma > quiet_sigma
    assert math.isfinite(volatile_sigma) and volatile_sigma > 0


def test_volatility_fit_is_unchanged_when_only_holdout_outcomes_change():
    original = cases()
    params = fit_volatility(original[:-24])
    altered = [dict(r) for r in original]
    for row in altered[-24:]:
        row["observed_index_f"] += 50
    assert fit_volatility(altered[:-24]) == params
    clean = choose_index_nowcast(original)
    spoiled = choose_index_nowcast(altered)
    assert clean["model"] == spoiled["model"]
    assert clean["parameters"] == spoiled["parameters"]
    assert spoiled["enabled"] is False


def test_volatility_requires_diverse_training_evidence():
    rows = cases(92)
    for row in rows:
        row["slow_slope"] = row["fast_slope"] + .1
    with pytest.raises(ValueError, match="INSUFFICIENT_REGIME_DIVERSITY"):
        fit_volatility(rows)
    with pytest.raises(ValueError, match="INSUFFICIENT_REGIME"):
        fit_volatility(cases(60))


def test_volatility_malformed_fit_or_live_input_never_controls_uncertainty():
    params = fit_volatility(cases(110))
    for invalid in (
        {**params, "volatility_gain": float("nan")},
        {**params, "volatility_gain": -1.0},
        {**params, "volatility_gain": 10.0},
        {**params, "sigma_scale": 0.0},
    ):
        assert not validate_volatility(invalid)
        assert predict_volatility(53., .8, 1., 3., .5, invalid) == (53., .8)
    assert predict_volatility(53., .8, float("nan"), 3., .5, params) == (53., .8)


def test_volatility_candidate_can_change_real_nowcast_distribution_when_validated():
    decision = choose_index_nowcast(cases())
    assert decision["enabled"] is True
    assert decision["model"] == VOLATILITY_MODEL
    assert validate_volatility(decision["parameters"])
    assert decision["validation"]["candidate_crps"] < decision["validation"]["baseline_crps"]
    lo_mean, lo_sigma = nowcast_prediction(55., .8, 1., 1., .5, decision)
    hi_mean, hi_sigma = nowcast_prediction(55., .8, 1., 4., .5, decision)
    assert hi_sigma > lo_sigma
    assert lo_mean != hi_mean


def test_live_forecast_exposes_distinct_v8_model_version_and_remains_paper_only(monkeypatch):
    import json
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from kalshi_edge.weather import hourly

    now = datetime(2026,10,5,17,30,tzinfo=timezone.utc)
    observed = now-timedelta(minutes=2)
    raw = {"t":int(observed.timestamp()*1000),
           "v":52.,"status":"normal","contributors":4}
    conn = sqlite3.connect(":memory:")
    hourly._init(conn)
    conn.execute("""INSERT INTO weather_hourly_index
       (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
       VALUES(?,?,?,?,?,?,?,?)""",
       ("chicago",raw["t"],(observed+timedelta(minutes=1)).isoformat(),
        52.,"normal","chicago-temperature-v1.1","a"*64,json.dumps(raw)))
    fitted = fit_volatility(cases(120))
    monkeypatch.setattr(hourly,"index_nowcast_decision",
        lambda *args, **kwargs:{
            "enabled":True,"status":"HISTORICAL_INDEX_HOLDOUT_IMPROVED_UNCERTIFIED",
            "history_hours":120,"validation_hours":24,
            "model":VOLATILITY_MODEL,"parameters":fitted,
            "live_slopes":(1.5,-.5,52.,raw["t"]),"validation":None})
    forecast = hourly._forecast(conn,"chicago",now)
    assert forecast["model_kind"]=="HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8"
    assert forecast["index_nowcast_selected_model"]==VOLATILITY_MODEL
    assert forecast["pre_physical_model_kind"]==forecast["model_kind"]
    assert forecast["calibration_certified"] is False
    assert forecast["real_orders_enabled"] is False


def test_v8_must_not_displace_a_v7_model_that_passes_when_regime_sigma_fails(monkeypatch):
    """The V7 champion remains available if a V8 challenger overfits sigma."""
    from kalshi_edge.weather import hourly_nowcast as impl

    def controlled_scores(rows, model, scale, params=None):
        if len(rows) > 24:  # training scores favor the V8 challenger
            crps = (.1 if model == VOLATILITY_MODEL else
                    .2 if model == impl.ADAPTIVE_MODEL else .5)
            return {"mae_f":.4, "crps":crps, "coverage_90":.9}
        if model is None:
            return {"mae_f":1.2, "crps":1.0, "coverage_90":.90}
        if model == impl.ADAPTIVE_MODEL:
            return {"mae_f":.7, "crps":.6, "coverage_90":.88}
        if model == VOLATILITY_MODEL:
            return {"mae_f":.7, "crps":1.05, "coverage_90":.9}
        return {"mae_f":1.1, "crps":.95, "coverage_90":.85}

    monkeypatch.setattr(impl, "_score", controlled_scores)
    chosen = impl.choose_index_nowcast(cases())
    assert chosen["model"] == impl.ADAPTIVE_MODEL
    assert chosen["enabled"] is True
    assert chosen["validation"]["candidate_crps"] == .6
