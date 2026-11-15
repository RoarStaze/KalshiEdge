"""Explicit local worker: continue active classifier weights, seal and evaluate.

This mode cannot access trading credentials or place exchange orders. Training
uses only verified settled forward checkpoints that have NEVER been evaluated
by an earlier lockbox. Rejected warm-start candidates remain resumable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

from . import artifact, evaluate, model_continuation as c, training
from .final_forecast import MINIMUM_FINAL_CONFIDENCE
from .metrics import probability_metrics


EVIDENCE_DIR = Path("models/continuation/evidence")
CHECKPOINT_POINTER = Path("models/continuation/latest.json")


def _canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_previous_checkpoint(
    root: Path, parent_sha: str,
) -> tuple[artifact.ModelBundle, training.FittedBootstrapPipeline] | None:
    path = root / CHECKPOINT_POINTER
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # If an earlier experiment became active, never continue an obsolete
        # checkpoint whose parent has changed.
        if data.get("active_parent_sha256") != parent_sha:
            return None
        digest = data["experiment_sha256"]
        if (type(digest) is not str or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise ValueError("invalid saved checkpoint SHA")
        bundle = artifact.load_model_bundle(
            root / "models" / "experiments" / f"{digest}.json"
        )
        if (bundle.stage != "experiment"
                or bundle.bundle_sha256 != digest
                or bundle.input_hashes.get("continuation/active_parent_bundle") != parent_sha):
            raise ValueError("saved checkpoint is not bound to current active model")
        pipeline = pickle.loads(artifact.serialized_pipeline_bytes(bundle))
        if not isinstance(pipeline, training.FittedBootstrapPipeline):
            raise ValueError("saved continued classifier has invalid pipeline type")
        return bundle, pipeline
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise c.ContinuationError("previous continued checkpoint is invalid") from exc


def _save_verified_development(
    root: Path, rows: list, parent_sha: str,
) -> tuple[str, str]:
    material = {
        "schema": "continued-active-model-480-development-v1",
        "active_parent_sha256": parent_sha,
        "row_count": len(rows),
        "verified_rows": [row.model_dump(mode="json") for row in rows],
    }
    blob = _canonical(material)
    digest = _hash(blob)
    relative = EVIDENCE_DIR / f"{digest}.json"
    target = root / relative
    if target.is_file():
        if target.read_bytes() != blob:
            raise c.ContinuationError("development evidence hash was modified")
    else:
        artifact._atomic_write(target, blob)
    return relative.as_posix(), digest


def run_continuation(root: Path, *, git_sha: str,
                     progress=None) -> dict[str, Any]:
    """Fit from old weights, then compare once on later official settlements."""
    from . import live, model_identity
    root = Path(root).resolve()
    active, live_pipeline = live.load_default_model(root)
    parent_sha = active.bundle_sha256
    pairs, consumed_end, _audit = c.source_pairs(
        root, active_hash=parent_sha,
        required_features=live_pipeline.required_feature_names,
    )
    if len(pairs) < c.MIN_NEW_MARKETS:
        raise c.ContinuationError(
            f"Only {len(pairs)}/{c.MIN_NEW_MARKETS} genuinely new settled "
            "active-model 480-second markets; previously evaluated reports "
            "cannot become fresh validation again"
        )
    dev = pairs[:-c.CALIBRATION_MARKETS-c.VALIDATION_MARKETS]
    cal = pairs[-c.CALIBRATION_MARKETS-c.VALIDATION_MARKETS:-c.VALIDATION_MARKETS]
    valid = pairs[-c.VALIDATION_MARKETS:]
    if (len(dev) < c.MIN_DEVELOPMENT
            or any({row.label_yes for row, _ in part} != {0, 1}
                   for part in (dev, cal, valid))):
        raise c.ContinuationError(
            "New development/calibration/validation periods must each have "
            "two verified settlement outcome classes"
        )
    bounds = artifact.SplitBoundaries(
        train_start_ts_ns=dev[0][0].checkpoint_ts_ns,
        train_end_ts_ns=dev[-1][0].checkpoint_ts_ns,
        calibration_start_ts_ns=cal[0][0].checkpoint_ts_ns,
        calibration_end_ts_ns=cal[-1][0].checkpoint_ts_ns,
        lockbox_start_ts_ns=valid[0][0].checkpoint_ts_ns,
        lockbox_end_ts_ns=valid[-1][0].checkpoint_ts_ns,
    )
    if bounds.train_start_ts_ns <= consumed_end:
        raise c.ContinuationError("continued training overlaps a consumed lockbox")
    if live.load_default_bundle(root).bundle_sha256 != parent_sha:
        raise c.ContinuationError("active model changed before continued fit")

    previous = _load_previous_checkpoint(root, parent_sha)
    fit_from = live_pipeline if previous is None else previous[1]

    if progress is not None:
        progress("FITTING")
    new_pipeline = c.continue_pipeline(fit_from, [row for row, _ in dev])
    if (new_pipeline.structural_model is not fit_from.structural_model
            or new_pipeline.calibrator is not fit_from.calibrator
            or new_pipeline.stacker is not fit_from.stacker):
        raise c.ContinuationError("continued fit unexpectedly replaced frozen pipeline components")
    development_path, development_sha = _save_verified_development(
        root, [row for row, _ in dev], parent_sha,
    )
    serialized = pickle.dumps(new_pipeline, protocol=5)
    metadata = {
        "policy": "active-model-fitted-classifier-continuation-v1",
        "active_parent_sha256": parent_sha,
        "earlier_checkpoint_sha256": previous[0].bundle_sha256 if previous else None,
        "development_sha256": development_sha,
        "extra_boosting_rounds": c.NEW_BOOSTING_ROUNDS,
        "logistic_iterations": c.LOGISTIC_ITERATIONS,
    }
    experiment = active.model_copy(update={
        "stage": "experiment",
        "git_sha": git_sha,
        "boundaries": bounds,
        # New continued classifier metrics are ONLY measured on the new
        # holdout below. Never mislabel the old parent development metrics
        # as results of this newly trained model.
        "metrics": {},
        "input_hashes": {
            **active.input_hashes,
            "continuation/active_parent_bundle": parent_sha,
            development_path: development_sha,
        },
        "training_config_hash": _hash(_canonical(metadata)),
        "serialized_pipeline_b64": base64.b64encode(serialized).decode("ascii"),
        "serialized_pipeline_sha256": _hash(serialized),
        "source_experiment_sha256": None,
        "promotion_rule": None,
        "promotion_reason_codes": (),
        "bundle_sha256": None,
    })
    # Immutably seal the new experiment BEFORE reading any held-out outcome.
    experiment_path = artifact.save_model_bundle(
        experiment, root / "models" / "experiments"
    )
    sealed = artifact.load_model_bundle(experiment_path)

    if progress is not None:
        progress("SCORING")
    rows = [row for row, _ in valid]
    labels = [row.label_yes for row in rows]
    incumbent_probabilities = [float(final.p_yes) for _, final in valid]
    with evaluate._holdout_executor() as executor:
        candidate_probabilities = new_pipeline.predict(
            rows, structural_executor=executor,
        )["candidate"]
    kalshi_probabilities = [float(row.features["kalshi_mid"]) for row in rows]

    if progress is not None:
        progress("COMPARING")
    causality = evaluate.audit_dataset_rows(
        [row for row, _ in dev]
        + [row for row, _ in cal] + rows
    )
    if not causality.passed:
        raise c.ContinuationError("new 480-second development/validation feature causality failed")
    candidate_score = probability_metrics(labels, candidate_probabilities)
    incumbent_score = probability_metrics(labels, incumbent_probabilities)
    kalshi_score = probability_metrics(labels, kalshi_probabilities)
    report = evaluate.BootstrapEvaluationReport(
        bundle_sha256=sealed.bundle_sha256,
        dataset_sha256=development_sha,
        model_version=active.model_version,
        lockbox_start_ts_ns=bounds.lockbox_start_ts_ns,
        lockbox_end_ts_ns=bounds.lockbox_end_ts_ns,
        evaluated_rows=len(rows),
        excluded_rows=0,
        feature_schema_match=True,
        leakage_audit=artifact.LeakageEvidence(
            passed=True, finding_count=0, finding_codes=(),
        ),
        required_ablations_present=bool(active.ablations),
        metrics={
            "candidate": artifact.metric_snapshot(candidate_score),
            "incumbent_current": artifact.metric_snapshot(incumbent_score),
            "kalshi_prior": artifact.metric_snapshot(kalshi_score),
            "retrain_480_candidate": artifact.metric_snapshot(candidate_score),
            "retrain_480_incumbent": artifact.metric_snapshot(incumbent_score),
            "retrain_480_kalshi": artifact.metric_snapshot(kalshi_score),
        },
        decision_metrics={
            "retrain_480_candidate": evaluate.checkpoint_gate_snapshot(
                labels, candidate_probabilities,
                minimum_confidence=MINIMUM_FINAL_CONFIDENCE,
            ),
            "retrain_480_incumbent": evaluate.checkpoint_gate_snapshot(
                labels, incumbent_probabilities,
                minimum_confidence=MINIMUM_FINAL_CONFIDENCE,
            ),
        },
        requires_retrain_480_gate=True,
        comparison_rule=evaluate.PROMOTION_RULE_ID,
    )
    if progress is not None:
        progress("PROMOTION_GATE")
    if live.load_default_bundle(root).bundle_sha256 != parent_sha:
        raise c.ContinuationError("active model changed before promotion")
    outcome = evaluate._finalize_evaluation(root, experiment_path, report)

    result: dict[str, Any] = {
        "status": "PROMOTED" if outcome.decision.promoted else "TRAINED_NOT_PROMOTED",
        "promoted": outcome.decision.promoted,
        "experiment_sha256": sealed.bundle_sha256,
        "active_parent_sha256": parent_sha,
        "retrained_entire_model": False,
        "continued_classifier_components": True,
        "development_markets": len(dev),
        "calibration_markets": len(cal),
        "validation_markets": len(valid),
        "candidate_gate": report.decision_metrics["retrain_480_candidate"].model_dump(mode="json"),
        "incumbent_gate": report.decision_metrics["retrain_480_incumbent"].model_dump(mode="json"),
        "gate_confidence": MINIMUM_FINAL_CONFIDENCE,
        "evaluation_report": str(outcome.report_path),
        "reason_codes": list(outcome.decision.reason_codes),
    }
    if outcome.decision.promoted:
        (root / CHECKPOINT_POINTER).unlink(missing_ok=True)
        result["promoted_model_number"] = model_identity.current_model_identity(root)["model_number"]
    else:
        # Keep the fitted candidate for the NEXT real incremental update.
        # Once the evaluation report is sealed, no previously scored cohort
        # can become a fresh lockbox in subsequent rounds.
        evaluate._atomic_json(root / CHECKPOINT_POINTER, {
            "active_parent_sha256": parent_sha,
            "experiment_sha256": sealed.bundle_sha256,
            "evaluation_report": str(outcome.report_path),
            "validation_end_ts_ns": bounds.lockbox_end_ts_ns,
        })
        result["note"] = (
            "Continued fitted classifier weights saved for the next round. "
            "Existing model unchanged because the later independent 63% "
            "coverage-first promotion checks did not pass."
        )
    return result
