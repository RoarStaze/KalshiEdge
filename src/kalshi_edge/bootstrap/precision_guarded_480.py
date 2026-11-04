"""Training-only precision guarded confidence rescue (offline research, no live changes).

Selects a fixed threshold on a development holdout, verifies on a
subsequent development holdout, and only then scores historical calibration.
No fitted weights, trade orders or live policy edits are produced.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .confidence_reliability_480 import wilson_interval
from .reliability_480_core import (
    BASELINE_GATE, CORRECTNESS_C, DIRECTION_C, FOLDS, MIN_OOF,
    classifier, gate_metrics, reliability_features,
)
from .specialist_480 import (
    FEATURES, MIN_TRAIN_MARKETS, MIN_VALIDATION_MARKETS, NS,
    select_eight_minute_rows,
)
from .types import FeatureRow

# Never choose/tune against pre-inspected 2,900-market calibration labels.
THRESHOLD_GRID = (.875, .9, .925, .95, .975)
MIN_SELECTION_RESCUES = 40
MIN_CONFIRMATION_RESCUES = 20


def _validate_paired(
    labels: Sequence[int], direction_p: Sequence[float],
    meta_p: Sequence[float],
) -> None:
    if not (len(labels) == len(direction_p) == len(meta_p)):
        raise ValueError("unequal labels, direction predictions, or meta predictions")
    gate_metrics(labels, direction_p, [True] * len(labels))
    if any(
        type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
        for p in meta_p
    ):
        raise ValueError("invalid estimated correctness probability")


def _policies(
    labels: Sequence[int], direction_p: Sequence[float],
    meta_p: Sequence[float], threshold: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _validate_paired(labels, direction_p, meta_p)
    if type(threshold) not in (int, float) or threshold not in THRESHOLD_GRID:
        raise ValueError("unsupported predeclared rescue threshold")
    baseline = [2 * abs(p - .5) >= BASELINE_GATE for p in direction_p]
    rescue = [not b and m >= threshold for b, m in zip(baseline, meta_p)]
    union = [b or r for b, r in zip(baseline, rescue)]
    return (
        gate_metrics(labels, direction_p, baseline),
        gate_metrics(labels, direction_p, rescue),
        gate_metrics(labels, direction_p, union),
    )


def _qualified_guard(
    baseline: dict[str, Any],
    rescues: dict[str, Any],
    hybrid: dict[str, Any],
    *,
    min_rescues: int,
) -> tuple[bool, float | None]:
    lower, _ = wilson_interval(rescues["correct"], rescues["qualified"])
    approved = (
        rescues["qualified"] >= min_rescues
        and baseline["accuracy"] is not None
        and rescues["accuracy"] is not None
        and hybrid["accuracy"] is not None
        and lower is not None
        and rescues["accuracy"] > baseline["accuracy"]
        and lower > baseline["accuracy"]
        and hybrid["accuracy"] > baseline["accuracy"]
        and hybrid["coverage"] > baseline["coverage"]
        and hybrid["correct_per_market"] > baseline["correct_per_market"]
    )
    return bool(approved), lower


def choose_guarded_threshold(
    labels: Sequence[int],
    direction_p: Sequence[float],
    meta_p: Sequence[float],
) -> dict[str, Any]:
    """Only pass chronological *development* holdout; never calibration.

    Every candidate is fixed in advance. A candidate must add 40 or more
    rescues and beat the baseline's conditional accuracy even at the
    Wilson 95% *lower* bound for rescues.
    """
    _validate_paired(labels, direction_p, meta_p)
    grid: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    for threshold in THRESHOLD_GRID:
        baseline, rescues, hybrid = _policies(
            labels, direction_p, meta_p, threshold,
        )
        pass_guard, lower = _qualified_guard(
            baseline, rescues, hybrid, min_rescues=MIN_SELECTION_RESCUES,
        )
        candidate = {
            "threshold": threshold,
            "passes": pass_guard,
            "rescue_markets": rescues["qualified"],
            "rescue_correct": rescues["correct"],
            "rescue_incorrect": rescues["incorrect"],
            "rescue_wilson_lower": lower,
            "baseline_accuracy": baseline["accuracy"],
            "hybrid_accuracy": hybrid["accuracy"],
            "hybrid_coverage": hybrid["coverage"],
        }
        grid.append(candidate)
        if pass_guard and (
            chosen is None
            or (candidate["rescue_correct"], threshold)
            > (chosen["rescue_correct"], chosen["threshold"])
        ):
            chosen = candidate
    first = _policies(labels, direction_p, meta_p, THRESHOLD_GRID[0])[0]
    return {
        "status": (
            "THRESHOLD_SELECTED" if chosen is not None
            else "NO_SAFE_DEVELOPMENT_THRESHOLD"
        ),
        "baseline": first,
        "selected_threshold": (
            chosen["threshold"] if chosen else None
        ),
        "selected_rescue_correct": (
            chosen["rescue_correct"] if chosen else 0
        ),
        "selected_rescue_incorrect": (
            chosen["rescue_incorrect"] if chosen else 0
        ),
        "selected_rescue_wilson_lower": (
            chosen["rescue_wilson_lower"] if chosen else None
        ),
        "grid": grid,
        "selection_objective": (
            "Maximize added CORRECT forecasts, subject to >=40 rescues "
            "and Wilson-95%-lower rescue hit rate above baseline hit rate. "
            "Tie break: highest confidence threshold."
        ),
    }


def confirm_guard(
    labels: Sequence[int],
    direction_p: Sequence[float],
    meta_p: Sequence[float],
    threshold: float,
) -> dict[str, Any]:
    """Later untouched development fold: confirmation, never optimization."""
    baseline, rescues, hybrid = _policies(
        labels, direction_p, meta_p, threshold,
    )
    passes, lower = _qualified_guard(
        baseline, rescues, hybrid,
        min_rescues=MIN_CONFIRMATION_RESCUES,
    )
    return {
        "passes": passes,
        "threshold": threshold,
        "baseline": baseline,
        "rescues": rescues,
        "hybrid": hybrid,
        "rescue_wilson_95_lower": lower,
        "confirmation_is_not_calibration_tuning": True,
    }
