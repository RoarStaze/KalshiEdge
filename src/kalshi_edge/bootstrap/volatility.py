from __future__ import annotations

"""Causal, volatility-aware challenger for the locked 240-second forecast.

A restrained analytic approximation to the final-minute *average* is blended
into the incumbent's calibrated probability. Its weight is selected using
historical, time-ordered calibration rows only. The lockbox is never consulted.
"""

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from .metrics import ProbabilityMetrics, probability_metrics
from .types import FeatureRow

VOLATILITY_POLICY = "volatility-final-minute-average-blend-v1"
WEIGHTS = (0.0, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50)
MIN_CALIBRATION_MARKETS = 80
# Realized-volatility measurements can be missing from some historical bars.
# A missing estimate uses the incumbent's probability (never guessed/filled).
# Require sufficient real observed markets in BOTH chronological partitions.
MIN_USABLE_VOLATILITY_MARKETS = 80
MIN_USABLE_FIT_MARKETS = 40
MIN_USABLE_VALIDATION_MARKETS = 20
MIN_USABLE_FRACTION_PER_PARTITION = 0.30
FINAL_LOCK_SECONDS = 240.0
CHECKPOINT_TOLERANCE_SECONDS = 1.0


class VolatilityChallengerError(ValueError):
    """The candidate is invalid or cannot demonstrate pre-lockbox value."""


@dataclass(frozen=True)
class VolatilityEstimate:
    probability_above: float
    expected_move_pct: float
    volatility_per_second: float
    effective_seconds: float


def analytic_estimate(features: Mapping[str, float]) -> VolatilityEstimate | None:
    """Normal approximation for a 60-second time average of a future BTC price.

    For Brownian log returns, variance of the mean of the final 60 seconds is
    approximately sigma**2 * (T - 2 * 60 / 3), for T >= 60 seconds. This is a
    deliberately simple estimate—not a replacement for Kalshi's official
    60-second BRTI settlement calculation or a claimed market probability.
    """

    try:
        btc = float(features["btc_close"])
        strike = float(features["strike"])
        remaining = float(features["seconds_remaining"])
        rv60 = float(features.get("btc_realized_vol_60s", 0.0))
        rv300 = float(features.get("btc_realized_vol_300s", 0.0))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    if (
        not all(math.isfinite(x) for x in (btc, strike, remaining))
        or btc <= 0.0 or strike <= 0.0 or remaining < 60.0
    ):
        return None

    # Zero/NaN can mean missing candle history in earlier feature builds.
    # Use the existing independent 300s estimate when the 60s one is absent;
    # never synthesize future volatility, and never use the zero value as
    # evidence of zero market risk. If both are unusable, no overlay applies.
    valid60 = math.isfinite(rv60) and rv60 > 0.0
    valid300 = math.isfinite(rv300) and rv300 > 0.0
    if not valid60 and not valid300:
        return None
    sigma = (
        .75 * rv60 + .25 * rv300 if valid60 and valid300
        else rv60 if valid60 else rv300
    )
    sigma = max(0.000005, min(0.01, sigma))
    effective = max(1.0, remaining - 40.0)
    standard_deviation = sigma * math.sqrt(effective)
    # Equal outcomes are not assumed from volatility alone. Drift and basis
    # uncertainty are learned by the incumbent model, not guessed here.
    log_distance = math.log(btc / strike)
    z = (log_distance - 0.5 * sigma * sigma * effective) / standard_deviation
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    probability = max(0.001, min(0.999, cdf))
    return VolatilityEstimate(
        probability_above=probability,
        expected_move_pct=standard_deviation * 100.0,
        volatility_per_second=sigma,
        effective_seconds=effective,
    )


@dataclass(frozen=True)
class VolatilityOverlay:
    weight: float
    policy: str = VOLATILITY_POLICY

    def __post_init__(self) -> None:
        if self.policy != VOLATILITY_POLICY:
            raise VolatilityChallengerError("unknown volatility overlay policy")
        if not math.isfinite(self.weight) or self.weight not in WEIGHTS or self.weight <= 0.0:
            raise VolatilityChallengerError("volatility overlay requires a predeclared positive blend weight")

    def predict(self, base_probability: float, features: Mapping[str, float]) -> float:
        if not math.isfinite(base_probability) or not 0.0 <= base_probability <= 1.0:
            raise VolatilityChallengerError("invalid incumbent probability")
        estimated = analytic_estimate(features)
        if estimated is None:
            return float(base_probability)
        adjusted = (1.0 - self.weight) * base_probability + self.weight * estimated.probability_above
        return min(1.0, max(0.0, adjusted))


@dataclass(frozen=True)
class VolatilitySelection:
    overlay: VolatilityOverlay | None
    fit_markets: int
    validation_markets: int
    fit_baseline: ProbabilityMetrics
    validation_baseline: ProbabilityMetrics
    validation_challenger: ProbabilityMetrics
    selected_weight: float
    accepted: bool
    reason: str
    usable_fit_markets: int = 0
    usable_validation_markets: int = 0
    unavailable_markets: int = 0


def is_final_checkpoint(row: FeatureRow) -> bool:
    remaining = row.features.get("seconds_remaining", -1)
    return math.isfinite(remaining) and abs(remaining - FINAL_LOCK_SECONDS) <= CHECKPOINT_TOLERANCE_SECONDS


def _metrics(rows: Sequence[FeatureRow], probabilities: Sequence[float]) -> ProbabilityMetrics:
    return probability_metrics([row.label_yes for row in rows], probabilities)


def select_volatility_overlay(
    rows: Sequence[FeatureRow],
    base_probabilities: Sequence[float],
) -> VolatilitySelection:
    """Select blend weight on early markets; require later-period validation.

    Rows MUST already be restricted to 240-second calibration checkpoints, one
    per whole market, with no held-out lockbox labels or observations.
    """

    if len(rows) != len(base_probabilities):
        raise VolatilityChallengerError("calibration probabilities and rows are not aligned")
    if len(rows) < MIN_CALIBRATION_MARKETS:
        raise VolatilityChallengerError(
            f"at least {MIN_CALIBRATION_MARKETS} historic 240-second calibration markets are required"
        )
    if any(not is_final_checkpoint(row) for row in rows):
        raise VolatilityChallengerError("non-final checkpoint leaked into volatility candidate selection")
    tickers = [row.market_ticker for row in rows]
    if len(set(tickers)) != len(rows):
        raise VolatilityChallengerError("each calibration market may contribute only one final forecast row")
    if any(not math.isfinite(p) or not 0.0 <= p <= 1.0 for p in base_probabilities):
        raise VolatilityChallengerError("incumbent predictions are not finite probabilities")
    # Score the entire chronological cohort against the incumbent so
    # missing-volatility markets cannot be silently dropped to inflate
    # apparent model quality. Overlay.predict() returns the incumbent
    # probability unchanged on those markets.
    availability = [analytic_estimate(row.features) is not None for row in rows]
    indices = sorted(range(len(rows)), key=lambda i: (rows[i].checkpoint_ts_ns, rows[i].market_ticker))
    split = len(indices) * 2 // 3
    train_ids, validation_ids = indices[:split], indices[split:]
    usable_fit = sum(availability[i] for i in train_ids)
    usable_validation = sum(availability[i] for i in validation_ids)
    total_usable = usable_fit + usable_validation
    if (
        total_usable < MIN_USABLE_VOLATILITY_MARKETS
        or usable_fit < max(MIN_USABLE_FIT_MARKETS, math.ceil(MIN_USABLE_FRACTION_PER_PARTITION * len(train_ids)))
        or usable_validation < max(
            MIN_USABLE_VALIDATION_MARKETS,
            math.ceil(MIN_USABLE_FRACTION_PER_PARTITION * len(validation_ids)),
        )
    ):
        raise VolatilityChallengerError(
            "insufficient observed historical BTC volatility at 240s checkpoint: "
            f"usable={total_usable}/{len(rows)}, "
            f"fit={usable_fit}/{len(train_ids)}, "
            f"validation={usable_validation}/{len(validation_ids)}; "
            "no values imputed and lockbox untouched. "
            "Backfill real BTC candles and rebuild the verified dataset."
        )
    fit_rows = [rows[i] for i in train_ids]
    validate_rows = [rows[i] for i in validation_ids]
    for group in (fit_rows, validate_rows):
        if set(row.label_yes for row in group) != {0, 1}:
            raise VolatilityChallengerError("both outcome classes are required in each chronological selection block")
    if fit_rows[-1].checkpoint_ts_ns >= validate_rows[0].checkpoint_ts_ns:
        raise VolatilityChallengerError("volatility calibration time windows overlap")

    fit_baseline = _metrics(fit_rows, [base_probabilities[i] for i in train_ids])
    validation_baseline = _metrics(validate_rows, [base_probabilities[i] for i in validation_ids])
    # Only the earlier window selects a weight; the later block is an explicit
    # reject/accept gate. That later block does NOT choose a different weight.
    candidates = []
    for weight in WEIGHTS:
        if weight == 0:
            probs = [base_probabilities[i] for i in train_ids]
        else:
            overlay = VolatilityOverlay(weight)
            probs = [overlay.predict(base_probabilities[i], rows[i].features) for i in train_ids]
        score = _metrics(fit_rows, probs)
        candidates.append((score.log_loss, score.brier, weight))
    _, _, selected = min(candidates)
    overlay = VolatilityOverlay(selected) if selected > 0 else None
    validation_predictions = [
        base_probabilities[i] if overlay is None
        else overlay.predict(base_probabilities[i], rows[i].features)
        for i in validation_ids
    ]
    challenger = _metrics(validate_rows, validation_predictions)
    improves = (
        overlay is not None
        and challenger.brier + 1e-5 < validation_baseline.brier
        and challenger.log_loss + 1e-5 < validation_baseline.log_loss
        and challenger.ece <= validation_baseline.ece + 0.02
    )
    return VolatilitySelection(
        overlay=overlay if improves else None,
        fit_markets=len(train_ids),
        validation_markets=len(validation_ids),
        fit_baseline=fit_baseline,
        validation_baseline=validation_baseline,
        validation_challenger=challenger,
        selected_weight=selected,
        accepted=improves,
        reason="PASSED_CHRONOLOGICAL_CALIBRATION" if improves else "NO_RELIABLE_VOLATILITY_IMPROVEMENT",
        usable_fit_markets=usable_fit,
        usable_validation_markets=usable_validation,
        unavailable_markets=len(rows) - total_usable,
    )
