from __future__ import annotations

"""Causal BRTI strike-distance risk feature for V6 shadow research.

Diagnostic feature, not a probability or a trade signal. All observations
used must be available by the requested prediction timestamp.
"""

from dataclasses import dataclass
import math
from collections.abc import Sequence

NS = 1_000_000_000


@dataclass(frozen=True)
class BrtiStrikeRisk:
    sigma_per_sqrt_second: float
    adjusted_distance: float
    observed_seconds: float
    observation_count: int


def compute_brti_strike_risk(
    observations: Sequence[tuple[int, float]],
    *,
    checkpoint_ts_ns: int,
    brti_value: float,
    strike: float,
    seconds_remaining: float = 240.0,
) -> BrtiStrikeRisk | None:
    """Return a pre-checkpoint volatility-adjusted strike distance, if supported.

    Uses a 120s trailing window. Adjacent samples require 0.5-5.0s spacing;
    at least 31 points and 60s of usable pairs are required. Newer observations
    are never read. This is NOT a calibrated correctness probability.
    """
    if (
        type(checkpoint_ts_ns) is not int
        or checkpoint_ts_ns <= 0
        or not all(math.isfinite(v) for v in (brti_value, strike, seconds_remaining))
        or brti_value <= 0
        or strike <= 0
        or seconds_remaining < 60
    ):
        return None

    cutoff = checkpoint_ts_ns - 120 * NS
    seen: dict[int, float] = {}
    for ts, value in observations:
        if type(ts) is not int or ts < cutoff or ts >= checkpoint_ts_ns:
            continue
        if not math.isfinite(value) or value <= 0 or ts in seen:
            return None
        seen[ts] = value

    points = sorted([*seen.items(), (checkpoint_ts_ns, brti_value)])
    if len(points) < 31:
        return None

    squared_log_returns = 0.0
    usable_seconds = 0.0
    for (ta, va), (tb, vb) in zip(points, points[1:]):
        dt = (tb - ta) / NS
        if not 0.5 <= dt <= 5.0:
            continue
        r = math.log(vb / va)
        squared_log_returns += r * r
        usable_seconds += dt

    if usable_seconds < 60 or squared_log_returns <= 0:
        return None
    sigma = math.sqrt(squared_log_returns / usable_seconds)
    effective = max(1.0, seconds_remaining - 40.0)
    distance = abs(math.log(brti_value / strike)) / (sigma * math.sqrt(effective))
    if not math.isfinite(distance):
        return None
    return BrtiStrikeRisk(
        sigma_per_sqrt_second=sigma,
        adjusted_distance=distance,
        observed_seconds=usable_seconds,
        observation_count=len(points),
    )
