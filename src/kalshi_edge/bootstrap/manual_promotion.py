"""Explicit, auditable acceptance of a verified accuracy-favored *rejected* model.

Never called by automatic training or live prediction. Does not read raw
lockbox data or re-run scoring; it consumes only the existing immutable
one-time report and hash-sealed experiment artifact.

Usage:
  python -m kalshi_edge.bootstrap.manual_promotion preview
  python -m kalshi_edge.bootstrap.manual_promotion activate
      --expected-experiment-sha <64-hex> --accept-manual-risk
  python -m kalshi_edge.bootstrap.manual_promotion rollback
      --expected-active-hash <64-hex> --accept-manual-risk
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import artifact, evaluate, live, model_identity, promotion_audit, training

MANUAL_RULE_ID = evaluate.MANUAL_OVERRIDE_RULE_ID
ALLOWED_REASONS = evaluate.MANUAL_ALLOWED_REJECTION_REASONS


def _sha(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 64 or
            any(ch not in "0123456789abcdef" for ch in value.lower())):
        raise ValueError("expected full 64-character verified model SHA-256")
    return value.lower()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"verified model report/pointer is missing or invalid: {path.name}") from exc
    if not isinstance(obj, dict):
        raise ValueError("verified model report/pointer must be a JSON object")
    return obj


def _validated_current(root: Path):
    pointer = _read_json(root / "models" / "default.json")
    try:
        active = live.load_default_bundle(root)
    except Exception as exc:
        raise ValueError("current promoted model pointer failed validation") from exc
    if (pointer.get("bundle_sha256") != active.bundle_sha256
            or pointer.get("promotion_rule") != active.promotion_rule):
        raise ValueError("current promoted model pointer provenance mismatch")
    number = pointer.get("model_number", 1)
    if type(number) is not int or number < 1 or number >= 1_000_000:
        raise ValueError("current promoted model number invalid")
    return pointer, active


def _verified_saved_candidate(root: Path) -> dict[str, Any]:
    """Read only the most recent completed *baseline* rejected training report."""
    state = _read_json(root / "training_runs" / "current.json")
    if state.get("stage") != "COMPLETE":
        raise ValueError("manual activation requires a completed training run; do not interrupt a worker")
    if state.get("mode") != "baseline" or state.get("decision") != "VALIDATION_FAILED":
        raise ValueError("manual activation requires the completed rejected baseline candidate")
    if state.get("promoted") is not False:
        raise ValueError("already promoted candidate cannot use manual rejection override")
    digest = _sha(state.get("experiment_sha256"))
    exact_report = (root / "reports" / f"evaluation-{digest}.json").resolve()
    declared = state.get("evaluation_report")
    if not isinstance(declared, str) or Path(declared).resolve() != exact_report:
        raise ValueError("saved evaluation report path mismatch; refusing arbitrary source path")
    report_data = _read_json(exact_report)
    experiment_path = (root / "models" / "experiments" / f"{digest}.json").resolve()
    if experiment_path.parent != (root / "models" / "experiments").resolve():
        raise ValueError("experiment path outside expected hash-sealed experiment directory")
    try:
        experiment = artifact.load_model_bundle(experiment_path)
    except Exception as exc:
        raise ValueError("candidate hash-sealed experiment is unavailable or corrupt") from exc
    if experiment.stage != "experiment" or experiment.bundle_sha256 != digest:
        raise ValueError("candidate is not an original unpromoted experiment")
    if (experiment.model_version != training.MODEL_VERSION or
            experiment.feature_schema_version != training.FEATURE_SCHEMA_VERSION):
        raise ValueError("candidate uses an incompatible model/feature schema")

    try:
        report = evaluate.BootstrapEvaluationReport.model_validate(report_data["report"])
        recorded = evaluate.PromotionDecision.model_validate(report_data["decision"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("saved evaluation report/decision cannot be verified") from exc
    if (report.bundle_sha256 != digest or
            report.dataset_sha256 != experiment.input_hashes.get("derived/features.parquet") or
            report.model_version != experiment.model_version or
            report.lockbox_start_ts_ns != experiment.boundaries.lockbox_start_ts_ns or
            report.lockbox_end_ts_ns != experiment.boundaries.lockbox_end_ts_ns or
            report.comparison_rule not in (evaluate.PROMOTION_RULE_ID, evaluate.LEGACY_PROMOTION_RULE_ID)):
        raise ValueError("saved report and trained experiment provenance mismatch")
    recomputed = evaluate.promotion_decision(report)
    if recomputed.model_dump(mode="json") != recorded.model_dump(mode="json"):
        raise ValueError("saved automatic promotion decision mismatch with immutable report")
    if recorded.promoted:
        raise ValueError("manual activation requires original automatic promotion rejection")
    reasons = set(recorded.reason_codes)
    if not reasons or not reasons.issubset(ALLOWED_REASONS):
        raise ValueError(
            "manual promotion cannot override integrity, leakage, calibration, "
            "benchmark or other non-accuracy/coverage safety rejection reasons"
        )
    if not report.leakage_audit.passed or not report.feature_schema_match:
        raise ValueError("data integrity or feature schema safety gate failed")
    if not report.required_ablations_present or not experiment.leakage_audit.passed:
        raise ValueError("training audit safety gate failed")

    # This is a report-only audit; it never opens or replays the consumed lockbox.
    try:
        audit = promotion_audit.summarize_saved_report(report_data)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("saved paired 480s audit failed integrity validation") from exc
    if (not isinstance(audit, dict) or audit.get("status") != "PAIRED_AVAILABLE"
            or not isinstance(audit.get("paired_480"), dict)):
        raise ValueError("saved independent same-market paired 480s audit required")
    if (audit["market_count"] < 80 or
            audit["overall_480_accuracy_delta_pp"] <= 0 or
            audit["qualified_accuracy_delta_pp"] is None or
            audit["qualified_accuracy_delta_pp"] <= 0):
        raise ValueError("candidate did not show higher same-market and qualified accuracy")
    paired = audit["paired_480"]
    if (paired.get("candidate_only_correct", 0)
            <= paired.get("incumbent_only_correct", 0)):
        raise ValueError("saved paired audit does not show a directional accuracy improvement")
    return {
        "experiment": experiment,
        "report": report,
        "decision": recorded,
        "audit": audit,
        "sha": digest,
        "report_path": exact_report,
        "experiment_path": experiment_path,
    }


def preview(root: Path) -> dict[str, Any]:
    """No filesystem writes, model loads, training, report recomputation or orders."""
    root = Path(root).resolve()
    verified = _verified_saved_candidate(root)
    pointer, active = _validated_current(root)
    audit = verified["audit"]
    if active.promotion_rule == MANUAL_RULE_ID and active.source_experiment_sha256 != verified["sha"]:
        raise ValueError("another manual model is active; rollback before a new override")
    return {
        "eligible": True,
        "experiment_sha256": verified["sha"],
        "currently_active_model_sha256": active.bundle_sha256,
        "currently_active_model_number": pointer.get("model_number", 1),
        "rejection_codes": list(verified["decision"].reason_codes),
        "paired_accuracy_delta_pp": audit["overall_480_accuracy_delta_pp"],
        "qualified_accuracy_delta_pp": audit["qualified_accuracy_delta_pp"],
        "coverage_delta_pp": audit["coverage_delta_pp"],
        "evaluated_markets": audit["market_count"],
        "automatic_promotion_passed": False,
        "activation_requires_explicit_opt_in": True,
        "real_money_profit_improvement_verified": False,
    }


def activate(root: Path, *, expected_experiment_sha: str, acknowledge: bool) -> dict[str, Any]:
    """Manual opt-in, hash-pinned, atomic pointer switch; reversible."""
    if acknowledge is not True:
        raise ValueError("manual activation requires explicit risk acknowledgement")
    expected = _sha(expected_experiment_sha)
    root = Path(root).resolve()
    verified = _verified_saved_candidate(root)
    if verified["sha"] != expected:
        raise ValueError("expected experiment SHA differs from the saved completed run")
    original_pointer, active = _validated_current(root)
    if active.promotion_rule == MANUAL_RULE_ID:
        if active.source_experiment_sha256 == expected:
            return {"status": "ALREADY_ACTIVE", "active_hash": active.bundle_sha256}
        raise ValueError("another manually promoted model is active; rollback first")
    if active.promotion_rule not in (evaluate.PROMOTION_RULE_ID, evaluate.LEGACY_PROMOTION_RULE_ID):
        raise ValueError("current model is not a verified automatic promotion")
    report, experiment, decision = verified["report"], verified["experiment"], verified["decision"]
    promoted = experiment.model_copy(update={
        "stage": "promoted",
        "metrics": {
            **{f"development_{name}": value for name, value in experiment.metrics.items()},
            **{f"lockbox_{name}": value for name, value in report.metrics.items()},
        },
        "source_experiment_sha256": experiment.bundle_sha256,
        "promotion_rule": MANUAL_RULE_ID,
        "promotion_reason_codes": decision.reason_codes,
        "bundle_sha256": None,
    })
    promoted_path = artifact.save_model_bundle(
        promoted, root / "models" / "promoted",
    )
    sealed = artifact.load_model_bundle(promoted_path)
    next_number = model_identity.next_promoted_model_number(root, sealed.bundle_sha256)
    backup_path = root / "models" / "manual_backups" / f"{sealed.bundle_sha256}.json"
    if backup_path.exists():
        if _read_json(backup_path) != original_pointer:
            raise ValueError("existing manual promotion backup conflicts with current incumbent")
    else:
        evaluate._atomic_json(backup_path, original_pointer)
    audit_path = root / "reports" / f"manual-activation-{sealed.bundle_sha256}.json"
    audit_record = {
        "kind": "USER_DIRECTED_REJECTED_CANDIDATE_ACTIVATION",
        "manual_rule": MANUAL_RULE_ID,
        "original_auto_decision": "REJECTED",
        "original_rejection_reason_codes": list(decision.reason_codes),
        "source_experiment_sha256": experiment.bundle_sha256,
        "source_evaluation_report": verified["report_path"].relative_to(root).as_posix(),
        "activated_promoted_sha256": sealed.bundle_sha256,
        "previous_promoted_sha256": active.bundle_sha256,
        "accuracy_delta_pp": verified["audit"]["overall_480_accuracy_delta_pp"],
        "qualified_accuracy_delta_pp": verified["audit"]["qualified_accuracy_delta_pp"],
        "coverage_delta_pp": verified["audit"]["coverage_delta_pp"],
        "acknowledged_manual_risk": True,
        "automatic_promotion_passed": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    if audit_path.exists():
        previous_audit = _read_json(audit_path)
        # Do not silently overwrite the audit of an earlier activation.
        if previous_audit.get("previous_promoted_sha256") != active.bundle_sha256:
            raise ValueError("manual activation audit conflicts with active incumbent")
    else:
        evaluate._atomic_json(audit_path, audit_record)
    pointer = {
        "model_number": next_number,
        "previous_model_hash": active.bundle_sha256,
        "bundle_sha256": sealed.bundle_sha256,
        "path": promoted_path.relative_to(root).as_posix(),
        "source_experiment_sha256": experiment.bundle_sha256,
        "promotion_rule": MANUAL_RULE_ID,
    }
    evaluate._atomic_json(root / "models" / "default.json", pointer)
    return {
        "status": "MANUALLY_ACTIVATED",
        "model_number": next_number,
        "active_hash": sealed.bundle_sha256,
        "previous_hash": active.bundle_sha256,
        "automatic_promotion_passed": False,
        "original_rejection_reasons": list(decision.reason_codes),
        "restart_required": True,
        "rollback_requires_exact_active_hash": sealed.bundle_sha256,
    }


def rollback(root: Path, *, expected_active_hash: str, acknowledge: bool) -> dict[str, Any]:
    """Restore the verified previous pointer; no trained artifacts deleted."""
    if acknowledge is not True:
        raise ValueError("manual rollback requires explicit risk acknowledgement")
    expected = _sha(expected_active_hash)
    root = Path(root).resolve()
    current, active = _validated_current(root)
    if active.bundle_sha256 != expected:
        raise ValueError("expected active model SHA does not match current pointer")
    if active.promotion_rule != MANUAL_RULE_ID:
        raise ValueError("active model is not a manual override")
    backup = root / "models" / "manual_backups" / f"{expected}.json"
    if not backup.is_file():
        raise ValueError("verified manual model backup not found")
    previous = _read_json(backup)
    original_root = (root / "models" / "promoted").resolve()
    raw_path = previous.get("path")
    if not isinstance(raw_path, str):
        raise ValueError("manual backup pointer path invalid")
    previous_path = (root / raw_path).resolve()
    if previous_path.parent != original_root:
        raise ValueError("manual backup pointer outside promoted model directory")
    try:
        original = artifact.load_model_bundle(previous_path)
    except Exception as exc:
        raise ValueError("previous promoted model backup failed integrity verification") from exc
    if (previous.get("bundle_sha256") != original.bundle_sha256 or
            previous.get("promotion_rule") not in (evaluate.PROMOTION_RULE_ID, evaluate.LEGACY_PROMOTION_RULE_ID) or
            original.stage != "promoted" or
            original.promotion_rule not in (evaluate.PROMOTION_RULE_ID, evaluate.LEGACY_PROMOTION_RULE_ID) or
            original.model_version != training.MODEL_VERSION or
            original.feature_schema_version != training.FEATURE_SCHEMA_VERSION or
            current.get("previous_model_hash") != original.bundle_sha256):
        raise ValueError("manual rollback backup is incompatible or mismatched")
    if (type(previous.get("model_number", 1)) is not int or
            previous.get("model_number", 1) >= current.get("model_number", 1)):
        raise ValueError("manual rollback model history is inconsistent")
    evaluate._atomic_json(root / "models" / "default.json", previous)
    evaluate._atomic_json(
        root / "reports" / f"manual-rollback-{expected}.json",
        {
            "kind": "MANUAL_PROMOTION_ROLLBACK",
            "deactivated_promoted_sha256": expected,
            "restored_promoted_sha256": original.bundle_sha256,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "status": "ROLLED_BACK",
        "restored_hash": original.bundle_sha256,
        "restart_required": True,
    }


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path,
        default=BootstrapSettings().bootstrap_dir,
        help="existing KalshiEdge data/bootstrap root",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preview", help="read existing report and show manual eligibility; no writes")
    activation = commands.add_parser("activate", help="explicitly switch to rejected, verified challenger")
    activation.add_argument("--expected-experiment-sha", required=True)
    activation.add_argument("--accept-manual-risk", action="store_true")
    restore = commands.add_parser("rollback", help="restore original promoted model pointer")
    restore.add_argument("--expected-active-hash", required=True)
    restore.add_argument("--accept-manual-risk", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "preview":
        result = preview(args.root)
    elif args.command == "activate":
        result = activate(
            args.root, expected_experiment_sha=args.expected_experiment_sha,
            acknowledge=args.accept_manual_risk,
        )
    else:
        result = rollback(
            args.root, expected_active_hash=args.expected_active_hash,
            acknowledge=args.accept_manual_risk,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
