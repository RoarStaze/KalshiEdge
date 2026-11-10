"""Train and compare a fixed 480-second specialist on VERIFIED prospective evidence.

Unlike the legacy four-round mistake cycle, this path uses newly captured,
hash-verified, point-in-time 480s features joined with official settlements.
Labels from forward snapshots cannot be fabricated or reconstructed after
settlement. Training/validation splits are chronological; there is no access
to the candidate's future prospective lockbox. NEVER promotes a live model or
changes AutoTrade.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from .forward_feature_evidence_480 import _joined
from .frozen_specialist_480 import fit_frozen_specialist, score_frozen_specialist
from .final_history import _forecasts
from .metrics import probability_metrics
from .specialist_480 import MIN_TRAIN_MARKETS, select_eight_minute_rows
from .types import FeatureRow

MIN_VALIDATION_MARKETS = 40
REQUIRED_MARKETS = MIN_TRAIN_MARKETS + MIN_VALIDATION_MARKETS
CONFIDENCE_GATE = 0.69


def _eligible(rows: Sequence[FeatureRow]) -> tuple[list[FeatureRow], dict[str, int]]:
    # Never count duplicates or invalid/future-source evidence toward training.
    eligible, exclusions = select_eight_minute_rows(rows)
    return eligible, exclusions


def partition_forward_training(
    rows: Sequence[FeatureRow],
) -> tuple[list[FeatureRow], list[FeatureRow]]:
    """Predeclare 70/30 chronological partitions, respecting minimum sizes."""
    eligible, _ = _eligible(rows)
    if len(eligible) < REQUIRED_MARKETS:
        raise ValueError(f"Need at least {REQUIRED_MARKETS} verified, eligible 480s markets")
    split = min(max(MIN_TRAIN_MARKETS, int(len(eligible) * .70)),
                len(eligible) - MIN_VALIDATION_MARKETS)
    train, validation = eligible[:split], eligible[split:]
    if train[-1].checkpoint_ts_ns >= validation[0].checkpoint_ts_ns:
        raise ValueError("Overlapping 480s development/validation timestamps")
    return train, validation


def assess_forward_training_readiness(
    rows: Sequence[FeatureRow],
    audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report evidence coverage without converting old parquet data into labels."""
    eligible, excluded = _eligible(rows)
    result: dict[str, Any] = {
        "report": "VERIFIED_FORWARD_480_TRAINING_READINESS_V1",
        "status": "COLLECTING_VERIFIED_480_EVIDENCE",
        "ready": False,
        "eligible_480_markets": len(eligible),
        "required_markets": REQUIRED_MARKETS,
        "required_train_markets": MIN_TRAIN_MARKETS,
        "required_validation_markets": MIN_VALIDATION_MARKETS,
        "progress_percent": min(99, int(100 * min(1, len(eligible) / REQUIRED_MARKETS))),
        "verified_outcome_classes": len({row.label_yes for row in eligible}),
        "pending_settlements": int((audit or {}).get("pending_outcomes") or 0),
        "missing_snapshot_records": int((audit or {}).get("missing_snapshot_records") or 0),
        "settled_480s": int((audit or {}).get("settled_480s") or 0),
        "exclusions": excluded,
        "reason": (
            "Collect immutable 480s feature snapshots and matching official "
            "settlements; historical parquet market count is a different cohort."
        ),
        "live_model_changed": False,
    }
    if len(eligible) < REQUIRED_MARKETS:
        return result
    try:
        train, validation = partition_forward_training(eligible)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    if {row.label_yes for row in train} != {0, 1}:
        result["reason"] = "Forward 480s development cohort needs both official outcome classes"
        return result
    if {row.label_yes for row in validation} != {0, 1}:
        result["reason"] = "Later chronological 480s validation needs both official outcome classes"
        return result
    result.update(
        status="READY_FOR_OFFLINE_CHALLENGER_EVALUATION",
        ready=True, progress_percent=100,
        train_markets=len(train),
        validation_markets=len(validation),
        reason=(
            "Verified 480s development and later validation cohorts ready. "
            "The candidate can be evaluated, but cannot replace the live predictor "
            "without future prospective confirmation."
        ),
    )
    return result


def inspect_forward_training_readiness(root: Path) -> dict[str, Any]:
    rows, audit = _joined(root)
    return assess_forward_training_readiness(rows, audit)


def _gate(labels: Sequence[int], probs: Sequence[float], *,
          forbidden: Sequence[bool] | None = None,
          issued: Sequence[bool] | None = None) -> dict[str, Any]:
    if len(labels) != len(probs):
        raise ValueError("Mismatched forecast label cohort")
    if forbidden is not None and len(forbidden) != len(labels):
        raise ValueError("Mismatched risk veto cohort")
    if issued is not None and len(issued) != len(labels):
        raise ValueError("Mismatched incumbent issuance cohort")
    qualified = 0
    correct = 0
    for i, (label, p) in enumerate(zip(labels, probs)):
        if (issued is not None and not issued[i]) or (
            issued is None and (
                (forbidden is not None and forbidden[i])
                or 2 * abs(p - 0.5) < CONFIDENCE_GATE
            )
        ):
            continue
        qualified += 1
        correct += int((p >= 0.5) == bool(label))
    return {
        "settled_markets": len(labels),
        "qualified": qualified,
        "correct": correct,
        "skipped": len(labels) - qualified,
        "coverage": qualified / len(labels) if labels else None,
        "qualified_accuracy": correct / qualified if qualified else None,
    }


def evaluate_forward_candidate(
    rows: Sequence[FeatureRow],
    incumbent_by_ticker: Mapping[str, Mapping[str, Any]],
    *, frozen_at_ns: int,
) -> dict[str, Any]:
    """Fit once on earlier labels, evaluate only later verified market labels.

    Returned metrics are retrospective holdout evidence, NOT a profit estimate
    or an authorization to replace the default model.
    """
    readiness = assess_forward_training_readiness(rows)
    result: dict[str, Any] = {
        "report": "KALSHIEDGE_FORWARD_480_CHALLENGER_V1",
        "status": "NOT_READY",
        "ready_to_promote": False,
        "live_model_changed": False,
        "auto_trade_changed": False,
        "next_required_evidence": (
            "Prospective, newly settled markets captured after freezing the model "
            "must demonstrate higher coverage AND conditional accuracy, stable "
            "Brier/log loss, and fee-inclusive executable P/L before deployment."
        ),
        "readiness": readiness,
    }
    if not readiness["ready"]:
        result["reason"] = readiness["reason"]
        return result
    train, valid = partition_forward_training(rows)
    result.update(training_markets=len(train), validation_markets=len(valid),
                  training_last_checkpoint_ns=train[-1].checkpoint_ts_ns,
                  validation_first_checkpoint_ns=valid[0].checkpoint_ts_ns)
    if (type(frozen_at_ns) is not int
            or frozen_at_ns <= valid[-1].checkpoint_ts_ns):
        raise ValueError("Frozen snapshot must be created strictly after historical validation")

    # The digest represents only official-label-verified development features.
    # Holdout markets never contribute to the fitted coefficients/digest.
    training_data = [row.model_dump(mode="json") for row in train]
    digest = hashlib.sha256(json.dumps(
        training_data, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    model = fit_frozen_specialist(
        train, dataset_sha256=digest,
        frozen_at_ns=train[-1].checkpoint_ts_ns + 1,
    )
    # score_frozen_specialist refuses timestamps at/before fit boundary.
    paired = []
    for row in valid:
        prior = incumbent_by_ticker.get(row.market_ticker)
        if not isinstance(prior, Mapping):
            continue
        p = prior.get("p_yes")
        if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
            continue
        if prior.get("forecast") not in ("ABOVE", "BELOW", "SKIP"):
            continue
        candidate_p = score_frozen_specialist(model, row)
        paired.append((row.label_yes, candidate_p, float(p),
                       prior.get("coin_flip_assessment") == "HIGH",
                       prior["forecast"] != "SKIP"))
    result["matched_validation_markets"] = len(paired)
    if len(paired) < MIN_VALIDATION_MARKETS:
        result["status"] = "INSUFFICIENT_PAIRED_VALIDATION"
        result["reason"] = (
            f"{len(paired)}/{MIN_VALIDATION_MARKETS} later markets have verified "
            "settlements, eligible features and saved incumbent probabilities"
        )
        return result
    labels = [x[0] for x in paired]
    candidate = [x[1] for x in paired]
    incumbent = [x[2] for x in paired]
    veto = [x[3] for x in paired]
    issued = [x[4] for x in paired]
    candidate_score = probability_metrics(labels, candidate)
    incumbent_score = probability_metrics(labels, incumbent)
    candidate_gate = _gate(labels, candidate, forbidden=veto)
    incumbent_gate = _gate(labels, incumbent, issued=issued)
    baseline_accuracy = incumbent_gate["qualified_accuracy"]
    joint = (
        candidate_gate["qualified"] > incumbent_gate["qualified"]
        and candidate_gate["qualified_accuracy"] is not None
        and (baseline_accuracy is None
             or candidate_gate["qualified_accuracy"] >= baseline_accuracy)
        and candidate_score.brier <= incumbent_score.brier
        and candidate_score.log_loss <= incumbent_score.log_loss
    )
    result.update(
        status="VALIDATION_JOINT_GAIN" if joint else "NO_VALIDATED_JOINT_GAIN",
        candidate_probability_metrics={"markets": len(paired), **candidate_score.model_dump(mode="json")},
        incumbent_probability_metrics={"markets": len(paired), **incumbent_score.model_dump(mode="json")},
        candidate_69_gate=candidate_gate,
        incumbent_69_gate=incumbent_gate,
        matched_validation_markets=len(paired),
        candidate_model_sha256=model["sha256"],
        training_development_sha256=digest,
        candidate_would_improve_joint_objective_retrospectively=joint,
        note=(
            "Frozen six-feature logistic candidate, zero hyperparameter or threshold search "
            "on later validation. Repeated retrospective comparisons are not independent "
            "proof of future gains. No automatic live promotion or trades."
        ),
    )
    return result


def evaluate_verified_forward_candidate(root: Path) -> dict[str, Any]:
    """Read only local, independently verified 480s labels and saved incumbent calls."""
    root = Path(root).resolve()
    rows, _audit = _joined(root)
    finals = {
        x.market_ticker: {
            "market_ticker": x.market_ticker, "p_yes": x.p_yes,
            "forecast": x.forecast, "coin_flip_assessment": x.coin_flip_assessment,
        } for x in _forecasts(root) if x.checkpoint_seconds == 480
    }
    return evaluate_forward_candidate(rows, finals, frozen_at_ns=time.time_ns())


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    args = parser.parse_args(argv)
    try:
        report = evaluate_verified_forward_candidate(args.root)
    except (OSError, ValueError) as exc:
        report = {
            "report": "KALSHIEDGE_FORWARD_480_CHALLENGER_V1",
            "status": "EVALUATION_REFUSED", "reason": str(exc),
            "ready_to_promote": False, "live_model_changed": False,
            "auto_trade_changed": False,
        }
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0 if report["status"] in ("VALIDATION_JOINT_GAIN", "NO_VALIDATED_JOINT_GAIN") else 2


if __name__ == "__main__":
    raise SystemExit(main())
