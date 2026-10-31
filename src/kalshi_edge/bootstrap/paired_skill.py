"""Paired, day-cluster bootstrap for a model versus checkpoint market odds.

Descriptive research only; not a model promotion rule or a risk-adjusted
trading signal. The uncertainty estimate does not prove independence across
calendar days and MUST NOT be called a prospective holdout.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
import random
from typing import Sequence


def _quantile(sorted_values: list[float], p: float) -> float:
    position = (len(sorted_values) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _log_loss(probability: float, label: int) -> float:
    p = min(1.0 - 1e-15, max(1e-15, probability))
    return -(label * math.log(p) + (1 - label) * math.log1p(-p))


def paired_market_skill_interval(
    records: Sequence[tuple[datetime, float, float, int]],
    *,
    min_markets: int = 50,
    min_days: int = 7,
    resamples: int = 500,
    seed: int = 137,
) -> dict:
    """Score AI-minus-market advantage using paired observations.

    Each row: (decision_time UTC-aware, AI P(YES), market midpoint P(YES),
    observed YES in {0,1}). Positive scores mean the AI's *scoring* was
    better on these matched markets. Sampling entire UTC days retains
    within-day correlations, but adjacent days can also be dependent.
    """
    if any(type(x) is not int or x < 1 for x in (min_markets, min_days, resamples)):
        raise ValueError("Minimum sample and resamples must be positive integers")
    if resamples > 10000 or type(seed) is not int:
        raise ValueError("Invalid bootstrap configuration")

    groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
    total = 0
    for item in records:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            raise ValueError("Each paired observation must have four fields")
        dt, ai, market, label = item
        if (not isinstance(dt, datetime) or dt.tzinfo is None
                or dt.utcoffset() is None
                or type(label) is not int or label not in (0, 1)):
            raise ValueError("Invalid settled market date or outcome")
        if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
               for p in (ai, market)):
            raise ValueError("Invalid AI or market probability")
        model_brier = (ai - label) ** 2
        market_brier = (market - label) ** 2
        brier_advantage = market_brier - model_brier
        log_advantage = _log_loss(market, label) - _log_loss(ai, label)
        day = dt.astimezone(timezone.utc).date().isoformat()
        groups[day].append((brier_advantage, log_advantage))
        total += 1

    days = len(groups)
    base = {
        "status": "INSUFFICIENT_SAMPLE",
        "paired_markets": total,
        "distinct_days": days,
        "minimum_required_markets": min_markets,
        "minimum_required_days": min_days,
        "resamples": resamples,
        "seed": seed,
        "observed_brier_skill": None,
        "observed_log_loss_skill": None,
        "brier_skill_ci_95": None,
        "log_loss_skill_ci_95": None,
        "independent_validation": False,
        "warning": (
            "Descriptive paired-day block bootstrap, NOT a prospective holdout "
            "or an executable-price comparison. Day clusters may still be "
            "dependent. A positive CI is not proof of future profit."
        ),
    }
    if not total:
        return base

    pairs = [pair for pairs in groups.values() for pair in pairs]
    base["observed_brier_skill"] = sum(pair[0] for pair in pairs) / total
    base["observed_log_loss_skill"] = sum(pair[1] for pair in pairs) / total
    if total < min_markets or days < min_days:
        return base

    rng = random.Random(seed)
    day_keys = sorted(groups)
    sampled_brier: list[float] = []
    sampled_log: list[float] = []
    for _ in range(resamples):
        sampled = [groups[day_keys[rng.randrange(days)]] for _ in range(days)]
        total_count = sum(len(cluster) for cluster in sampled)
        sampled_brier.append(sum(x for cluster in sampled for x, _ in cluster) / total_count)
        sampled_log.append(sum(x for cluster in sampled for _, x in cluster) / total_count)

    sampled_brier.sort()
    sampled_log.sort()
    base.update(
        status="DESCRIPTIVE_DAY_CLUSTER_BOOTSTRAP",
        brier_skill_ci_95={
            "low": _quantile(sampled_brier, .025),
            "high": _quantile(sampled_brier, .975),
        },
        log_loss_skill_ci_95={
            "low": _quantile(sampled_log, .025),
            "high": _quantile(sampled_log, .975),
        },
    )
    return base
