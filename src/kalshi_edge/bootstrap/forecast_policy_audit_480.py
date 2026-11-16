"""Read-only paired comparison of the old and requested 480s SKIP policies.

This command does NOT fit or compare prediction models. It replays the SAME
timestamp-locked historical YES probabilities against independently saved,
officially settled outcomes, keeping the existing BRTI coin-flip veto.
No exchange credentials, live API, or order submission is used.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .config import BootstrapSettings
from .final_forecast import (
    COIN_FLIP_MAX_RISK_Z,
    FINAL_CHECKPOINT_SECONDS,
    MINIMUM_FINAL_CONFIDENCE,
    MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
)
from .final_history import (
    SettlementIntegrityError,
    _close_ns,
    _forecasts,
    _load_saved_settlement,
)

# Existing settled 480s records may have one of these original SKIP reasons.
# Never reinterpret a missing, late, unhealthy, or invalid forecast as a
# retroactively eligible trade.
_GATE_REASONS = {
    None, "CONFIDENCE_BELOW_69_PERCENT", "CONFIDENCE_BELOW_63_PERCENT",
    "DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT", "COIN_FLIP_RISK_HIGH",
}
_EXCLUSIONS = (
    "non_480_checkpoint", "different_model", "unsettled",
    "invalid_settlement", "non_probability_skip", "missing_probability",
)

# Predeclared descriptive gate sweep. Never chosen or applied to live trading.
SWEEP_THRESHOLDS_PERCENT = (63, 68, 70, 73, 75, 78, 80, 82)


def _sweep_rows(
    total: int,
    thresholds: tuple[int, ...],
    counts: dict[int, list[int]],
) -> list[dict[str, Any]]:
    """Nested gate rows; incremental accuracy uses only each newly admitted band.

    For a lower threshold t and the next higher threshold h, the incremental
    cohort is (t/100, h/100], provided the saved BRTI veto did not apply.
    The largest gate has no higher comparison, so its marginal values are None.
    """
    rows: list[dict[str, Any]] = []
    for index, percent in enumerate(thresholds):
        qualified, correct = counts[percent]
        row = _summary(total, qualified, correct)
        higher = thresholds[index + 1] if index + 1 < len(thresholds) else None
        if higher is None:
            added = added_correct = added_incorrect = added_accuracy = None
        else:
            higher_qualified, higher_correct = counts[higher]
            added = qualified - higher_qualified
            added_correct = correct - higher_correct
            added_incorrect = added - added_correct
            if min(added, added_correct, added_incorrect) < 0:
                raise ValueError("Nested sweep thresholds yielded inconsistent counts")
            added_accuracy = added_correct / added if added else None
        row.update({
            "threshold_percent": percent,
            "threshold_probability": percent / 100.,
            "incremental_vs_higher_threshold_percent": higher,
            "incremental_qualified": added,
            "incremental_correct": added_correct,
            "incremental_incorrect": added_incorrect,
            "incremental_accuracy": added_accuracy,
            "confidence_interval_kind": "not_calculated",
        })
        rows.append(row)
    return rows


def _print_sweep_table(report: dict[str, Any]) -> None:
    """Console-only, readable on Windows PowerShell without extra libraries."""
    print("KalshiEdge 480-second historical probability-gate sweep")
    print("Model hash:", report["model_hash_filter"] or "ALL HISTORICAL MODELS (exploratory)")
    print("Status:", report["status"], "| Paired settled markets:", report["matched_settled_markets"])
    print("Strict rule: qualify only if predicted-side probability > gate; BRTI flip veto retained.")
    print("Incremental accuracy: accuracy of newly admitted markets vs next higher tested gate.")
    print()
    print(
        f'{"Gate":>6} {"Qualified":>9} {"Skipped":>8} {"Correct":>8} '
        f'{"Incorrect":>10} {"Accuracy":>10} {"Coverage":>10} '
        f'{"Added":>7} {"Incremental accuracy":>21}'
    )
    for row in report["sweep"]:
        accuracy = "-" if row["accuracy"] is None else f'{100 * row["accuracy"]:.2f}%'
        marginal = (
            "-" if row["incremental_accuracy"] is None
            else f'{100 * row["incremental_accuracy"]:.2f}%'
        )
        added = "-" if row["incremental_qualified"] is None else str(row["incremental_qualified"])
        print(
            f'{row["threshold_percent"]:>5}% {row["qualified"]:>9} {row["skipped"]:>8} '
            f'{row["correct"]:>8} {row["incorrect"]:>10} {accuracy:>10} '
            f'{100 * row["coverage"]:>9.2f}% {added:>7} {marginal:>21}'
        )
    print()
    print("Excluded (never silently interpreted as tradable):")
    for reason, count in report["excluded"].items():
        print(f"  {reason}: {count}")
    print()
    print("DESCRIPTIVE ONLY: not a model comparison or independent validation.")
    print(report["limitation"])


def _summary(total: int, qualified: int, correct: int) -> dict[str, Any]:
    return {
        "settled_markets": total,
        "qualified": qualified,
        "skipped": total - qualified,
        "correct": correct,
        "incorrect": qualified - correct,
        "coverage": qualified / total if total else 0.,
        "accuracy": correct / qualified if qualified else None,
    }


def evaluate_saved_policy(
    root: Path, *, model_hash: str | None,
    sweep_thresholds_percent: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Compare eligibility on strictly paired, immutable, settled checkpoints.

    The original model and its probabilities are the SAME for both policies.
    Do not call this a model improvement or use it to claim calibrated uplift.
    """
    root = Path(root).resolve()
    if sweep_thresholds_percent is not None:
        if (not sweep_thresholds_percent
                or tuple(sorted(set(sweep_thresholds_percent))) != sweep_thresholds_percent
                or any(type(t) is not int or not 50 < t < 100
                       for t in sweep_thresholds_percent)):
            raise ValueError("Sweep thresholds must be unique increasing integer percentages (51-99)")
        sweep_counts = {t: [0, 0] for t in sweep_thresholds_percent}
    else:
        sweep_counts = {}
    excluded = {name: 0 for name in _EXCLUSIONS}
    original_qual = original_correct = changed_qual = changed_correct = 0
    gained_correct = gained_wrong = 0
    matched = 0
    brier_sum = 0.
    for forecast in _forecasts(root):
        if forecast.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
            excluded["non_480_checkpoint"] += 1
            continue
        if model_hash is not None and forecast.model_hash != model_hash:
            excluded["different_model"] += 1
            continue
        try:
            outcome = _load_saved_settlement(root, forecast)
        except (SettlementIntegrityError, ValueError, OSError):
            excluded["invalid_settlement"] += 1
            continue
        if outcome is None:
            excluded["unsettled"] += 1
            continue
        if outcome.fetched_ts_ns <= _close_ns(forecast):
            excluded["invalid_settlement"] += 1
            continue
        if forecast.reason not in _GATE_REASONS:
            excluded["non_probability_skip"] += 1
            continue
        yes, no = forecast.p_yes, forecast.p_no
        if (type(yes) not in (int, float) or type(no) not in (int, float)
                or not math.isfinite(yes) or not math.isfinite(no)
                or not 0 <= yes <= 1 or not 0 <= no <= 1
                or abs(yes + no - 1) > 1e-6):
            excluded["missing_probability"] += 1
            continue
        if (forecast.directional_probability is not None
                and abs(forecast.directional_probability - max(yes, no)) > 1e-6):
            excluded["missing_probability"] += 1
            continue

        actual_yes = outcome.result == "yes"
        direction_correct = (yes >= .5) == actual_yes
        probability = max(yes, no)
        z = forecast.coin_flip_risk_z
        veto = (forecast.coin_flip_assessment == "HIGH"
                or (z is not None and z <= COIN_FLIP_MAX_RISK_Z))
        # Old: 63% model-strength (requires 81.5% predicted-side probability).
        old_qualifies = (
            not veto and 2 * abs(yes - .5) >= MINIMUM_FINAL_CONFIDENCE
        )
        # New: strict >63% predicted-direction probability, not confidence.
        new_qualifies = (
            not veto and probability > MINIMUM_FINAL_DIRECTIONAL_PROBABILITY
        )
        matched += 1
        brier_sum += (yes - int(actual_yes)) ** 2
        if old_qualifies:
            original_qual += 1
            original_correct += int(direction_correct)
        if new_qualifies:
            changed_qual += 1
            changed_correct += int(direction_correct)
        if new_qualifies and not old_qualifies:
            gained_correct += int(direction_correct)
            gained_wrong += int(not direction_correct)
        for threshold, tally in sweep_counts.items():
            if not veto and probability > threshold / 100.:
                tally[0] += 1
                tally[1] += int(direction_correct)

    current = _summary(matched, original_qual, original_correct)
    requested = _summary(matched, changed_qual, changed_correct)
    old_acc = current["accuracy"]
    new_acc = requested["accuracy"]
    report = {
        "status": "DESCRIPTIVE_ONLY" if matched else "NO_MATCHED_SETTLEMENTS",
        "policy": "480s-same-probability-decision-gates-v1",
        "model_hash_filter": model_hash,
        "matched_settled_markets": matched,
        "excluded": excluded,
        "current_strength_policy": current,
        "requested_directional_policy": requested,
        "changes": {
            "extra_qualifying": changed_qual - original_qual,
            "fewer_skips": current["skipped"] - requested["skipped"],
            "extra_correct": gained_correct,
            "extra_incorrect": gained_wrong,
            "coverage_change_percentage_points": (
                100 * (requested["coverage"] - current["coverage"])
            ),
            "accuracy_change_percentage_points": (
                None if old_acc is None or new_acc is None
                else 100 * (new_acc - old_acc)
            ),
        },
        "unchanged_model_brier_score": brier_sum / matched if matched else None,
        "probability_source": "UNCHANGED_SAVED_MODEL",
        "model_retraining": False,
        "coin_flip_veto": "PRESERVED",
        "limitation": (
            "This is not a comparison of trained predictors: both policies use "
            "the same immutable historical model probabilities. Selection "
            "coverage and qualified accuracy can change without improved "
            "probability calibration or signal quality. Markets in the model's "
            "training set are not fresh out-of-sample proof; verify on new "
            "settlements, and measure fees/fills separately."
        ),
    }
    if sweep_thresholds_percent is not None:
        report.update({
            "sweep_thresholds_percent": list(sweep_thresholds_percent),
            "sweep": _sweep_rows(matched, sweep_thresholds_percent, sweep_counts),
            "live_gate_changed": False,
        })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=BootstrapSettings().bootstrap_dir,
        help="Verified KalshiEdge bootstrap data directory",
    )
    parser.add_argument(
        "--model-hash", default=None,
        help="Only compare records issued by this historical promoted model",
    )
    parser.add_argument(
        "--all-models", action="store_true",
        help="Exploratory cross-model history; mixed versions are not a fair model comparison",
    )
    parser.add_argument(
        "--sweep", action="store_true",
        help="Read-only paired 63/68/70/73/75/78/80/82%% directional-probability gate sweep",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="With --sweep: output structured JSON instead of the human-readable table",
    )
    args = parser.parse_args(argv)
    if args.json and not args.sweep:
        parser.error("--json requires --sweep (the original audit is already JSON)")
    if args.all_models and args.model_hash:
        parser.error("--all-models cannot be combined with --model-hash")
    if args.all_models:
        model_hash = None
    elif args.model_hash is not None:
        model_hash = args.model_hash
    else:
        from .live import load_default_bundle
        try:
            model_hash = load_default_bundle(args.root).bundle_sha256
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(
                f"Cannot identify the active promoted model: {exc}. "
                "Supply --model-hash or --all-models for descriptive history."
            )
    report = evaluate_saved_policy(
        args.root, model_hash=model_hash,
        sweep_thresholds_percent=(SWEEP_THRESHOLDS_PERCENT if args.sweep else None),
    )
    if args.sweep and not args.json:
        _print_sweep_table(report)
    else:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
