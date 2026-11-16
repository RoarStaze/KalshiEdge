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


def evaluate_saved_policy(root: Path, *, model_hash: str | None) -> dict[str, Any]:
    """Compare eligibility on strictly paired, immutable, settled checkpoints.

    The original model and its probabilities are the SAME for both policies.
    Do not call this a model improvement or use it to claim calibrated uplift.
    """
    root = Path(root).resolve()
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

    current = _summary(matched, original_qual, original_correct)
    requested = _summary(matched, changed_qual, changed_correct)
    old_acc = current["accuracy"]
    new_acc = requested["accuracy"]
    return {
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
    args = parser.parse_args(argv)
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
    report = evaluate_saved_policy(args.root, model_hash=model_hash)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
