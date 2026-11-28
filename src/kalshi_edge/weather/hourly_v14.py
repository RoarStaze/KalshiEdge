"""WeatherEdge V14: bounded reversal-regime residual calibration over V13.

The V13 forecast remains the reference. V14 adds a single strongly-shrunk
turning-point correction and a training-only uncertainty regime adjustment.
No market outcomes, post-target observations or retrospective prices are
used as predictors. A separate chronological replay must establish value.
"""
from __future__ import annotations

import math

from .hourly_hybrid import MIN_TRAIN, _crps, _number, fit_hybrid, predict_hybrid, validate_hybrid

V14_MODEL = "REGIME_RESIDUAL_HYBRID_V14"
SIGMA_GAP_GRID = (0., .10, .20, .30, .40)
MIN_TRAINING = MIN_TRAIN


def _turning_feature(row: dict) -> tuple[float, float]:
    fast = _number(row["fast_slope"], -12., 12.)
    slow = _number(row["slow_slope"], -12., 12.)
    hours = _number(row["hours_ahead"], .05, 2.)
    gap = abs(fast-slow)
    # A directional bend is valuable when the recent and slower trends
    # genuinely oppose one another. Other cases have zero mean correction.
    turning = (max(-5., min(5., fast*hours)) *
               min(1., gap/4.) if fast*slow < 0. else 0.)
    disagreement = min(1., gap/4.)
    return turning, disagreement


def validate_v14(params: object) -> bool:
    if not isinstance(params, dict) or not validate_hybrid(params.get("v13")):
        return False
    try:
        correction = _number(params["turning_gain"], -0.70, 0.70)
        gap = _number(params["sigma_gap_gain"], 0., .40)
        return bool(math.isfinite(correction) and math.isfinite(gap))
    except (ValueError, TypeError, KeyError):
        return False


def predict_v14(row: dict, params: dict) -> tuple[float, float]:
    if not validate_v14(params):
        raise ValueError("INVALID_V14_PARAMETERS")
    mean, sigma = predict_hybrid(row, params["v13"])
    turning, disagreement = _turning_feature(row)
    current = _number(row["current_index_f"], -129., 159.)
    mean = current + max(-8., min(8., mean-current +
                                  params["turning_gain"]*turning))
    if not -130. < mean < 160.:
        raise ValueError("OUT_OF_RANGE_V14_FORECAST")
    sigma *= 1. + params["sigma_gap_gain"]*disagreement
    return mean, max(.05, sigma)


def fit_v14(rows: list[dict]) -> dict:
    """Fit one extra bounded nonlinear coefficient on older rows only."""
    if not isinstance(rows, list) or len(rows) < MIN_TRAINING:
        raise ValueError("INSUFFICIENT_V14_TRAINING")
    eligible = [row for row in rows if row.get("diurnal_delta_f") is not None]
    params_v13 = fit_hybrid(eligible)
    numerator = 0.
    denominator = 40.
    for row in eligible:
        baseline, _ = predict_hybrid(row, params_v13)
        actual = _number(row["observed_index_f"], -129., 159.)
        feature, _ = _turning_feature(row)
        error = actual-baseline
        weight = min(1., .75/max(.1, abs(error)))
        numerator += weight*feature*error
        denominator += weight*feature*feature
    gain = max(-.70, min(.70, numerator/denominator))
    candidate = {"v13": params_v13, "turning_gain": round(gain, 6),
                 "sigma_gap_gain": 0.}
    # Proper scoring rule, trained only on earlier labeled observations;
    # zero-gap remains an available baseline. No held-out hour is used.
    def cost(gap_gain: float) -> float:
        params = {**candidate, "sigma_gap_gain": gap_gain}
        total = 0.
        for row in eligible:
            mean, sigma = predict_v14(row, params)
            total += _crps(mean, sigma, _number(row["observed_index_f"], -129., 159.))
        return total/len(eligible) + .002*gap_gain
    best = min(SIGMA_GAP_GRID, key=cost)
    result = {**candidate, "sigma_gap_gain": best}
    if not validate_v14(result):
        raise ValueError("INVALID_FITTED_V14")
    return result
