"""Causal, regularized volatility-sensitive temperature uncertainty.

The mean is unchanged from the V7 adaptive nowcast. Only historical
first-received, same-lead Kalshi Index samples train the conditional sigma.
This is a *candidate*, not evidence of real-world calibration.
"""
from __future__ import annotations

import math

from .hourly_adaptive import fit_adaptive, predict_adaptive, validate_adaptive
from .predictor import normal_cdf

MIN_TRAIN = 80
MIN_REGIME = 16
GAINS = (.25, .5, .75, 1., 1.25)


def _strength(fast: float, slow: float) -> float:
    return min(1., abs(fast - slow) / 3.)


def validate_volatility(parameters: object) -> bool:
    if not validate_adaptive(parameters):
        return False
    gain = parameters.get("volatility_gain")
    return (
        isinstance(gain, (int, float))
        and not isinstance(gain, bool)
        and math.isfinite(gain)
        and 0. <= gain <= 1.25
    )


def volatility_sigma(baseline_sigma: float, fast: float, slow: float,
                     parameters: dict) -> float:
    """Return sigma in Fahrenheit, with a strict upper bound."""
    return max(.05, min(30., baseline_sigma * parameters["sigma_scale"] *
                    (1. + parameters["volatility_gain"] * _strength(fast, slow))))


def _crps(mean: float, sigma: float, actual: float) -> float:
    z = (actual - mean) / sigma
    phi = math.exp(-.5*z*z) / math.sqrt(2.*math.pi)
    return sigma * (z * (2.*normal_cdf(z, 0., 1.) - 1.) +
                    2.*phi - 1./math.sqrt(math.pi))


def fit_volatility(rows: list[dict]) -> dict:
    """Fit sigma from past labels only; caller reserves chronological holdout."""
    if len(rows) < MIN_TRAIN:
        raise ValueError("INSUFFICIENT_REGIME_TRAINING_CASES")
    # fit_adaptive validates the source-compatible temperature and slope ranges.
    base = fit_adaptive(rows)
    examples = []
    for row in rows:
        try:
            fast = float(row["fast_slope"])
            slow = float(row["slow_slope"])
            sigma = float(row["baseline_sigma_f"])
            actual = float(row["observed_index_f"])
            current = float(row["current_index_f"])
            hours = float(row["hours_ahead"])
            if not all(math.isfinite(v) for v in
                       (fast,slow,sigma,actual,current,hours)):
                raise ValueError("INVALID_REGIME_TRAINING_CASE")
            mean, _ = predict_adaptive(current, sigma, fast, slow, hours, base)
            x = _strength(fast, slow)
            examples.append((x, mean, sigma, actual))
        except (TypeError, KeyError, ValueError, OverflowError) as exc:
            raise ValueError("INVALID_REGIME_TRAINING_CASE") from exc
    if (sum(x <= .35 for x,_,_,_ in examples) < MIN_REGIME or
        sum(x >= .65 for x,_,_,_ in examples) < MIN_REGIME):
        raise ValueError("INSUFFICIENT_REGIME_DIVERSITY")

    choices = []
    for gain in GAINS:
        # The scale is fitted from TRAINING residuals only; shrink it toward
        # V7's regularized scale to reduce sparse-hour overconfidence.
        rms = math.sqrt(sum(
            ((actual - mean) / (sigma * (1. + gain*x)))**2
            for x,mean,sigma,actual in examples) / len(examples))
        scale = max(.75, min(1.65,
                            .25 * base["sigma_scale"] + .75 * rms))
        candidate = {**base, "sigma_scale":round(scale,6),
                     "volatility_gain":gain}
        score = sum(_crps(mean, volatility_sigma(sigma,
                        0., 3.*x, candidate), actual)
                    for x,mean,sigma,actual in examples) / len(examples)
        choices.append((score, gain, candidate))
    return min(choices, key=lambda item:(item[0],item[1]))[2]


def predict_volatility(current_f: float, sigma_f: float,
                       fast_slope: float, slow_slope: float,
                       hours_ahead: float, parameters: object
                       ) -> tuple[float,float]:
    """Malformed/unsupported live evidence preserves the original estimate."""
    if not validate_volatility(parameters):
        return current_f,sigma_f
    values=(current_f,sigma_f,fast_slope,slow_slope,hours_ahead)
    if (not all(isinstance(v,(int,float)) and not isinstance(v,bool) and
                math.isfinite(v) for v in values)
        or not (-130<current_f<160 and 0.<sigma_f<=20. and
                0.<hours_ahead<=2. and abs(fast_slope)<=12. and
                abs(slow_slope)<=12.)):
        return current_f,sigma_f
    mean, _ = predict_adaptive(current_f,sigma_f,fast_slope,slow_slope,
                               hours_ahead,parameters)
    return mean,volatility_sigma(sigma_f,fast_slope,slow_slope,parameters)
