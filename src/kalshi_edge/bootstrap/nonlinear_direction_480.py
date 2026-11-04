"""Fixed nonlinear 480s directional challenger, exclusively offline research.

A low-capacity gradient-boosted decision-tree classifier and the existing
six-feature logistic research baseline see precisely the same earliest
chronological training cases. No calibration or held-out labels are used
for threshold tuning, model selection or training. Both later development
blocks must exceed the fixed baseline in coverage and directional accuracy
before historical prelockbox calibration is even scored.

Neither model represents deployed bootstrap-hybrid-v5. No weights are saved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .metrics import probability_metrics
from .reliability_480_core import BASELINE_GATE, DIRECTION_C, classifier, gate_metrics
from .specialist_480 import FEATURES, MIN_VALIDATION_MARKETS, NS, select_eight_minute_rows
from .types import FeatureRow

MIN_DEVELOPMENT = 500
MIN_SCREEN = 100
MIN_CONFIRM = 100
MIN_QUALIFIED = 100
FIT_FRACTION = 0.60
SCREEN_FRACTION = 0.20


def _nonlinear_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(
        learning_rate=.05,
        max_iter=140,
        max_leaf_nodes=9,
        min_samples_leaf=80,
        l2_regularization=5.,
        max_bins=128,
        early_stopping=False,
        random_state=0,
    )


def _features(rows: Sequence[FeatureRow]) -> list[list[float]]:
    return [[r.features[name] for name in FEATURES] for r in rows]


def _groups(rows: Sequence[FeatureRow]) -> set[str]:
    return {r.split_group_id or r.market_ticker for r in rows}


def _result_row(labels: list[int], probabilities: list[float]) -> dict[str, Any]:
    qualify = [2 * abs(p - .5) >= BASELINE_GATE for p in probabilities]
    return {
        "gate_69": gate_metrics(labels, probabilities, qualify),
        "probability_metrics": probability_metrics(
            labels, probabilities,
        ).model_dump(mode="json", exclude={"reliability_bins"}),
    }


def _compare(
    rows: Sequence[FeatureRow],
    *,
    baseline_model: Any,
    nonlinear_model: Any,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot compare models on empty chronological block")
    x = _features(rows)
    labels = [r.label_yes for r in rows]
    baseline = [float(p) for p in baseline_model.predict_proba(x)[:, 1]]
    candidate = [float(p) for p in nonlinear_model.predict_proba(x)[:, 1]]
    b = _result_row(labels, baseline)
    c = _result_row(labels, candidate)
    base, challenger = b["gate_69"], c["gate_69"]
    objective = bool(
        challenger["qualified"] >= MIN_QUALIFIED
        and base["accuracy"] is not None
        and challenger["accuracy"] is not None
        and challenger["coverage"] > base["coverage"]
        and challenger["accuracy"] > base["accuracy"]
        and challenger["correct_per_market"] > base["correct_per_market"]
    )
    return {
        "markets":len(rows),
        "baseline_logistic": b,
        "candidate_nonlinear": c,
        "joint_objective_met": objective,
        "coverage_delta":challenger["coverage"]-base["coverage"],
        "conditional_accuracy_delta":(
            challenger["accuracy"]-base["accuracy"]
            if challenger["accuracy"] is not None
            and base["accuracy"] is not None else None
        ),
        "correct_per_market_delta":(
            challenger["correct_per_market"]-base["correct_per_market"]
        ),
    }
