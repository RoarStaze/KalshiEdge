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


def _features(rows: Sequence[FeatureRow]) -> list[list[float]]:
    return [[row.features[name] for name in FEATURES] for row in rows]


def _group(row: FeatureRow) -> str:
    return row.split_group_id or row.market_ticker


def evaluate_precision_guard(
    development_rows: Sequence[FeatureRow],
    calibration_rows: Sequence[FeatureRow],
) -> dict[str, Any]:
    """Separate OOF meta-fit, threshold choice, confirmation and calibration.

    Confidence thresholds are selected solely on development labels from
    OOF block 3. No calibration labels are read before a threshold survives
    the subsequent independent-in-time development block 4.
    """
    development, dev_ex = select_eight_minute_rows(development_rows)
    validation, val_ex = select_eight_minute_rows(calibration_rows)
    output: dict[str, Any] = {
        "report": "KXBTC15M_480S_DEVELOPMENT_ONLY_PRECISION_GUARD_V1",
        "status": "INSUFFICIENT_DIRECTION_TRAINING",
        "direction_fit_markets": len(development),
        "evaluation_markets": len(validation),
        "development_exclusions": dev_ex,
        "evaluation_exclusions": val_ex,
        "training_last_ns": (
            development[-1].checkpoint_ts_ns if development else None
        ),
        "evaluation_first_ns": (
            validation[0].checkpoint_ts_ns if validation else None
        ),
        "oof_folds": [],
        "oof_meta_fit_markets": 0,
        "threshold_selection_markets": 0,
        "internal_confirmation_markets": 0,
        "threshold_selection": None,
        "selected_threshold": None,
        "internal_confirmation": None,
        "baseline_69": None,
        "guarded_union": None,
        "additional_rescues": None,
        "joint_objective_retrospective": False,
        "safety": {
            "model_saved": False,
            "ready_to_promote": False,
            "live_trading_changed": False,
            "independent_holdout": False,
            "reserved_lockbox_opened": False,
        },
        "note": (
            "Only strictly historical, verified 480-second development rows "
            "may select a policy, which must pass a later development block "
            "before retrospective calibration scoring. Historical calibration "
            "has been inspected in earlier experiments: not independent. "
            "Original baseline in this experiment is the six-feature "
            "logistic, NOT deployed live V5. Both correct and incorrect "
            "rescues are always counted. No live policy changes or trades."
        ),
    }
    dev_groups = {_group(r) for r in development}
    valid_groups = {_group(r) for r in validation}
    if development and validation and (
        development[-1].checkpoint_ts_ns + 900 * NS
        > validation[0].checkpoint_ts_ns
        or dev_groups.intersection(valid_groups)
        or {r.market_ticker for r in development}.intersection(
            r.market_ticker for r in validation
        )
    ):
        output["status"] = "LEAKAGE_OR_OVERLAPPING_PERIODS"
        return output
    if len(development) < MIN_TRAIN_MARKETS:
        return output
    if len(validation) < MIN_VALIDATION_MARKETS:
        output["status"] = "INSUFFICIENT_EVALUATION"
        return output
    if (
        len(dev_groups) != len(development)
        or len(valid_groups) != len(validation)
    ):
        output["status"] = "AMBIGUOUS_MARKET_GROUPS"
        return output
    if len({r.label_yes for r in development}) != 2:
        output["status"] = "SINGLE_CLASS_DIRECTION_TARGET"
        return output

    initial = max(MIN_TRAIN_MARKETS, len(development) // 2)
    remaining = len(development) - initial
    if remaining < MIN_OOF:
        output["status"] = "INSUFFICIENT_OOF_TRAINING"
        return output
    boundaries = [
        initial + remaining * i // FOLDS
        for i in range(FOLDS + 1)
    ]
    folds: list[list[tuple[FeatureRow, float]]] = []
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        previous, holdout = development[:left], development[left:right]
        if not holdout:
            output["status"] = "INSUFFICIENT_OOF_TRAINING"
            return output
        if previous[-1].checkpoint_ts_ns + 900 * NS > holdout[0].checkpoint_ts_ns:
            output["status"] = "LEAKAGE_OR_OVERLAPPING_PERIODS"
            return output
        if len({r.label_yes for r in previous}) != 2:
            output["status"] = "SINGLE_CLASS_OOF_DIRECTION_TARGET"
            return output
        model = classifier(DIRECTION_C)
        model.fit(_features(previous), [r.label_yes for r in previous])
        probs = model.predict_proba(_features(holdout))[:, 1]
        folds.append(list(zip(holdout, map(float, probs))))
        output["oof_folds"].append({
            "fit_markets": len(previous),
            "oof_markets": len(holdout),
            "fit_last_ns": previous[-1].checkpoint_ts_ns,
            "oof_first_ns": holdout[0].checkpoint_ts_ns,
        })
    if len(folds) != 4:
        output["status"] = "INSUFFICIENT_OOF_TRAINING"
        return output

    meta_training = [*folds[0], *folds[1]]
    output["oof_meta_fit_markets"] = len(meta_training)
    output["threshold_selection_markets"] = len(folds[2])
    output["internal_confirmation_markets"] = len(folds[3])
    if len(meta_training) < MIN_OOF:
        output["status"] = "INSUFFICIENT_OOF_META_FIT"
        return output
    meta_targets = [
        int((p >= .5) == bool(row.label_yes))
        for row, p in meta_training
    ]
    output["meta_correctness_targets_correct"] = sum(meta_targets)
    output["meta_correctness_targets_incorrect"] = (
        len(meta_targets) - sum(meta_targets)
    )
    if len(set(meta_targets)) != 2:
        output["status"] = "SINGLE_CLASS_RELIABILITY_TARGET"
        return output
    correctness_model = classifier(CORRECTNESS_C)
    correctness_model.fit(
        [reliability_features(row, p) for row, p in meta_training],
        meta_targets,
    )

    def score_fold(fold: list[tuple[FeatureRow, float]]):
        rows = [r for r, _ in fold]
        p = [p for _, p in fold]
        correctness = [
            float(x) for x in correctness_model.predict_proba(
                [reliability_features(r, prob) for r, prob in fold]
            )[:, 1]
        ]
        return [r.label_yes for r in rows], p, correctness

    # Threshold selection sees ONLY block three. There is no code path
    # from calibration outcomes to selection.
    chosen = choose_guarded_threshold(*score_fold(folds[2]))
    output["threshold_selection"] = chosen
    threshold = chosen["selected_threshold"]
    if threshold is None:
        output["status"] = "NO_SAFE_DEVELOPMENT_THRESHOLD"
        return output
    output["selected_threshold"] = threshold

    # Later internal confirmation must pass unchanged before even looking
    # at the historical calibration labels.
    confirmation = confirm_guard(*score_fold(folds[3]), threshold)
    output["internal_confirmation"] = confirmation
    if not confirmation["passes"]:
        output["status"] = "FAILED_INTERNAL_CONFIRMATION"
        return output

    # The full direction model is fit ONLY on development. The meta model
    # remains trained solely on the first two development OOF blocks.
    final_direction = classifier(DIRECTION_C)
    final_direction.fit(
        _features(development), [r.label_yes for r in development]
    )
    direction_p = [
        float(p) for p in final_direction.predict_proba(
            _features(validation)
        )[:, 1]
    ]
    correctness_p = [
        float(p) for p in correctness_model.predict_proba(
            [reliability_features(r, p) for r, p in zip(
                validation, direction_p
            )]
        )[:, 1]
    ]
    labels = [r.label_yes for r in validation]
    base, rescue, union = _policies(
        labels, direction_p, correctness_p, threshold
    )
    output.update(
        status = "RESEARCH_CALIBRATION_ONLY",
        baseline_69 = base,
        guarded_union = union,
        additional_rescues = rescue,
        joint_objective_retrospective = bool(
            union["coverage"] > base["coverage"]
            and union["accuracy"] is not None
            and base["accuracy"] is not None
            and union["accuracy"] > base["accuracy"]
            and union["correct_per_market"] > base["correct_per_market"]
        ),
        net_new_correct_forecasts = rescue["correct"],
        net_new_incorrect_forecasts = rescue["incorrect"],
        net_new_qualifying_forecasts = rescue["qualified"],
    )
    return output


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
        development = set(context.plan.development_groups)
        calibration = set(context.plan.calibration_groups)
        report = evaluate_precision_guard(
            [r for r in rows if training._group(r) in development],
            [r for r in rows if training._group(r) in calibration],
        )
        report["verified_feature_dataset_sha256"] = context.input_hashes[
            "derived/features.parquet"
        ]
        report["partition"] = "VERIFIED_PRELOCKBOX_DEVELOPMENT_THEN_CALIBRATION"
    except (OSError, ValueError, training.TrainingError) as exc:
        report = {
            "status": "DATASET_UNAVAILABLE_OR_UNVERIFIED",
            "error": str(exc),
            "model_saved": False,
            "ready_to_promote": False,
            "reserved_lockbox_opened": False,
        }
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
