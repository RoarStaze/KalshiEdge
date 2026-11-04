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
from .paired_disagreement_480 import paired_disagreement_480
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
        "paired_disagreement": paired_disagreement_480(labels, baseline, candidate),
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


def evaluate_nonlinear_direction(
    development_rows: Sequence[FeatureRow],
    calibration_rows: Sequence[FeatureRow],
) -> dict[str, Any]:
    """Compare exactly two fixed models on two subsequent development blocks.

    Never scores any historical calibration outcome unless BOTH development
    comparisons meet the strict joint objective. No fitting after the
    original 60%-chronological training block.
    """
    development, dev_ex = select_eight_minute_rows(development_rows)
    historical, cal_ex = select_eight_minute_rows(calibration_rows)
    report: dict[str, Any] = {
        "report": "KXBTC15M_480S_FIXED_NONLINEAR_DIRECTION_CHALLENGER_V1",
        "status": "INSUFFICIENT_DEVELOPMENT",
        "development_markets": len(development),
        "historical_calibration_markets": len(historical),
        "development_exclusions": dev_ex,
        "calibration_exclusions": cal_ex,
        "fit_markets": 0,
        "development_screen_markets": 0,
        "development_confirm_markets": 0,
        "training_max_ns": None,
        "screen_min_ns": None,
        "screen_max_ns": None,
        "confirm_min_ns": None,
        "confirm_max_ns": None,
        "calibration_min_ns": historical[0].checkpoint_ts_ns if historical else None,
        "development_screen": None,
        "development_confirm": None,
        "historical_calibration": None,
        "confidence_gate": BASELINE_GATE,
        "features": list(FEATURES),
        "challenger": "HistGradientBoostingClassifier",
        "challenger_fixed_parameters": {
            "learning_rate": .05,
            "max_iter": 140,
            "max_leaf_nodes": 9,
            "min_samples_leaf": 80,
            "l2_regularization": 5.,
            "max_bins": 128,
            "early_stopping": False,
            "random_state": 0,
        },
        "model_search_performed": False,
        "threshold_changed": False,
        "joint_objective_retrospective": False,
        "safety": {
            "model_saved": False,
            "ready_to_promote": False,
            "live_trading_changed": False,
            "reserved_lockbox_opened": False,
            "independent_validation_proven": False,
        },
        "note": (
            "Fixed models and gate, read-only prelockbox research. "
            "The original is the six-feature logistic baseline, NOT deployed V5. "
            "Screen and confirm are two later development windows, both "
            "previously eligible for repeated historical experimentation; "
            "not independent forward validation. Historical calibration "
            "has also been inspected previously. No live accuracy claim, "
            "weights, threshold search or trading changes."
        ),
    }

    # Diagnose overlap before sample checks so reversed periods never get
    # misleadingly reported as only insufficient sample size.
    if development and historical and (
        development[-1].checkpoint_ts_ns + 900 * NS
        > historical[0].checkpoint_ts_ns
        or {r.market_ticker for r in development}.intersection(
            r.market_ticker for r in historical
        )
        or _groups(development).intersection(_groups(historical))
    ):
        report["status"] = "LEAKAGE_OR_OVERLAPPING_PERIODS"
        return report

    if len(development) < MIN_DEVELOPMENT:
        return report
    if len(historical) < MIN_VALIDATION_MARKETS:
        report["status"] = "INSUFFICIENT_CALIBRATION"
        return report
    if (
        len(_groups(development)) != len(development)
        or len(_groups(historical)) != len(historical)
    ):
        report["status"] = "AMBIGUOUS_MARKET_GROUPS"
        return report

    count = len(development)
    fit_end = int(count * FIT_FRACTION)
    screen_end = int(count * (FIT_FRACTION + SCREEN_FRACTION))
    fit, screen, confirm = (
        development[:fit_end],
        development[fit_end:screen_end],
        development[screen_end:],
    )
    report.update(
        fit_markets=len(fit),
        development_screen_markets=len(screen),
        development_confirm_markets=len(confirm),
        training_max_ns=fit[-1].checkpoint_ts_ns if fit else None,
        screen_min_ns=screen[0].checkpoint_ts_ns if screen else None,
        screen_max_ns=screen[-1].checkpoint_ts_ns if screen else None,
        confirm_min_ns=confirm[0].checkpoint_ts_ns if confirm else None,
        confirm_max_ns=confirm[-1].checkpoint_ts_ns if confirm else None,
    )

    if len(fit) < MIN_DEVELOPMENT * FIT_FRACTION or len(screen) < MIN_SCREEN or len(confirm) < MIN_CONFIRM:
        report["status"] = "INSUFFICIENT_DEVELOPMENT_BLOCKS"
        return report
    if (
        fit[-1].checkpoint_ts_ns + 900 * NS > screen[0].checkpoint_ts_ns
        or screen[-1].checkpoint_ts_ns + 900 * NS > confirm[0].checkpoint_ts_ns
        or confirm[-1].checkpoint_ts_ns + 900 * NS > historical[0].checkpoint_ts_ns
        or _groups(fit).intersection(_groups(screen))
        or _groups(fit).intersection(_groups(confirm))
        or _groups(screen).intersection(_groups(confirm))
    ):
        report["status"] = "LEAKAGE_OR_OVERLAPPING_PERIODS"
        return report
    if {r.label_yes for r in fit} != {0, 1}:
        report["status"] = "SINGLE_CLASS_TRAIN"
        return report

    logistic = classifier(DIRECTION_C)
    tree = _nonlinear_model()
    features = _features(fit)
    labels = [r.label_yes for r in fit]
    logistic.fit(features, labels)
    tree.fit(features, labels)
    # Same pair of frozen fitted estimators is evaluated on both windows.
    # No refitting or gate search after observing screen outcomes.
    first = _compare(screen, baseline_model=logistic, nonlinear_model=tree)
    second = _compare(confirm, baseline_model=logistic, nonlinear_model=tree)
    report["development_screen"] = first
    report["development_confirm"] = second
    if not (
        first["joint_objective_met"]
        and second["joint_objective_met"]
    ):
        report["status"] = "DEVELOPMENT_REJECTED"
        return report

    # This is already-inspected historical calibration; descriptive only.
    scored = _compare(historical, baseline_model=logistic, nonlinear_model=tree)
    report["historical_calibration"] = scored
    report["status"] = "RESEARCH_CALIBRATION_ONLY"
    report["joint_objective_retrospective"] = scored["joint_objective_met"]
    return report


def main(argv: list[str] | None = None) -> int:
    from . import training
    from .config import BootstrapSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        context = training._verified_context(root)
        rows = training._read_rows_before_lockbox(context)
        dev_groups = set(context.plan.development_groups)
        cal_groups = set(context.plan.calibration_groups)
        report = evaluate_nonlinear_direction(
            [r for r in rows if training._group(r) in dev_groups],
            [r for r in rows if training._group(r) in cal_groups],
        )
        report["dataset_sha256"] = context.input_hashes["derived/features.parquet"]
        report["partition"] = "VERIFIED_PRELOCKBOX_DEVELOPMENT_THEN_CALIBRATION"
    except (OSError, ValueError, training.TrainingError) as exc:
        report = {
            "status": "DATASET_UNAVAILABLE_OR_UNVERIFIED",
            "error": str(exc),
            "ready_to_promote": False,
            "model_saved": False,
            "reserved_lockbox_opened": False,
        }
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
