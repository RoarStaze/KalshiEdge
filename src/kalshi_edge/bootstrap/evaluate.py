from __future__ import annotations

"""Untouched lockbox evaluation, experiment persistence, and promotion policy."""

import json
import math
import os
import pickle
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from . import artifact, training
from .config import BootstrapSettings
from .leakage import audit_dataset_rows
from .metrics import probability_metrics
from .types import FeatureRow


class EvaluationError(RuntimeError):
    """Raised when a bootstrap model cannot be evaluated without violating the gate."""


REQUIRED_ABLATIONS = (
    "external_btc_features",
    "kalshi_flow_features",
    "structural_component",
    "residual_component",
    "xgboost_vs_lower_variance",
)

PROMOTION_RULE_ID = "kalshi-lockbox-v1:both-nonworse-one-improves-ece-bounded"
PROMOTION_MIN_IMPROVEMENT = 1e-6
PROMOTION_NONWORSE_TOLERANCE = 1e-12
PROMOTION_MAX_ECE_DEGRADATION = 0.02


class BootstrapTrainingReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_bundle_sha256: str
    experiment_path: Path
    report_path: Path
    train_rows: int = Field(ge=0)
    calibration_rows: int = Field(ge=0)
    lockbox_rows: int = Field(ge=0)
    excluded_feature_families: tuple[str, ...]
    excluded_components: tuple[str, ...]


class BootstrapEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    bundle_sha256: str
    dataset_sha256: str
    model_version: str
    lockbox_start_ts_ns: int = Field(gt=0)
    lockbox_end_ts_ns: int = Field(gt=0)
    evaluated_rows: int = Field(gt=0)
    excluded_rows: int = Field(ge=0)
    feature_schema_match: bool
    leakage_audit: artifact.LeakageEvidence
    required_ablations_present: bool
    metrics: dict[str, artifact.MetricSnapshot]
    comparison_rule: str


class PromotionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    promoted: bool
    superior_to_kalshi: bool
    reason_codes: tuple[str, ...]
    brier_improvement: float
    log_loss_improvement: float
    ece_change: float
    comparison_rule: str


class BootstrapEvaluationRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_path: Path
    report_path: Path
    decision: PromotionDecision
    promoted_path: Path | None
    default_path: Path | None


def _dominates(
    candidate: artifact.MetricSnapshot,
    benchmark: artifact.MetricSnapshot,
    *,
    min_improvement: float = PROMOTION_MIN_IMPROVEMENT,
) -> tuple[bool, bool, float, float]:
    brier_improvement = benchmark.brier - candidate.brier
    log_loss_improvement = benchmark.log_loss - candidate.log_loss
    nonworse = (
        candidate.brier <= benchmark.brier + PROMOTION_NONWORSE_TOLERANCE
        and candidate.log_loss <= benchmark.log_loss + PROMOTION_NONWORSE_TOLERANCE
    )
    improved = brier_improvement >= min_improvement or log_loss_improvement >= min_improvement
    return nonworse, improved, brier_improvement, log_loss_improvement


def ablation_decision(
    name: str,
    *,
    full_metrics: artifact.MetricSnapshot,
    ablated_metrics: artifact.MetricSnapshot,
) -> artifact.AblationEvidence:
    """Retain a family/component only when the full system adds later-period value."""
    nonworse, improved, _, _ = _dominates(full_metrics, ablated_metrics)
    retained = nonworse and improved
    reason = (
        "later-period probability metrics improved with this family/component"
        if retained
        else "excluded because the family/component did not add later-period probability value"
    )
    return artifact.AblationEvidence(
        name=name,
        full_metrics=full_metrics,
        ablated_metrics=ablated_metrics,
        retained=retained,
        reason=reason,
    )


def promotion_decision(report: BootstrapEvaluationReport) -> PromotionDecision:
    reasons: list[str] = []
    if report.comparison_rule != PROMOTION_RULE_ID:
        reasons.append("COMPARISON_RULE_MISMATCH")
    if not report.leakage_audit.passed:
        reasons.append("LEAKAGE_AUDIT_FAILED")
    if not report.feature_schema_match:
        reasons.append("FEATURE_SCHEMA_MISMATCH")
    if not report.required_ablations_present:
        reasons.append("REQUIRED_ABLATION_MISSING")

    candidate = report.metrics.get("candidate")
    prior = report.metrics.get("kalshi_prior")
    if candidate is None or prior is None:
        reasons.append("REQUIRED_BENCHMARK_MISSING")
        brier_improvement = 0.0
        log_loss_improvement = 0.0
        ece_change = 0.0
        superior = False
    else:
        nonworse, improved, brier_improvement, log_loss_improvement = _dominates(candidate, prior)
        ece_change = candidate.ece - prior.ece
        if not nonworse:
            reasons.append("KALSHI_COMPLEMENTARY_METRIC_DEGRADED")
        if not improved:
            reasons.append("NO_PREDECLARED_KALSHI_IMPROVEMENT")
        if ece_change > PROMOTION_MAX_ECE_DEGRADATION + PROMOTION_NONWORSE_TOLERANCE:
            reasons.append("CALIBRATION_MATERIALLY_DEGRADED")
        superior = nonworse and improved and ece_change <= PROMOTION_MAX_ECE_DEGRADATION + PROMOTION_NONWORSE_TOLERANCE

    promoted = superior and not reasons
    return PromotionDecision(
        promoted=promoted,
        superior_to_kalshi=superior and not reasons,
        reason_codes=tuple(reasons),
        brier_improvement=brier_improvement,
        log_loss_improvement=log_loss_improvement,
        ece_change=ece_change,
        comparison_rule=PROMOTION_RULE_ID,
    )


def _dataset_hash(bundle: artifact.ModelBundle) -> str:
    matches = [
        digest
        for path, digest in bundle.input_hashes.items()
        if path.endswith("derived/features.parquet") or path == "derived/features.parquet"
    ]
    if len(matches) != 1:
        raise EvaluationError("bundle must contain exactly one derived features dataset hash")
    return matches[0]


def _validate_prediction_map(predictions: Mapping[str, Sequence[float]], expected: int) -> dict[str, list[float]]:
    normalized: dict[str, list[float]] = {}
    for name, values in predictions.items():
        vector = [float(value) for value in values]
        if len(vector) != expected:
            raise EvaluationError(f"prediction length mismatch for component {name}")
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in vector):
            raise EvaluationError(f"invalid probability from component {name}")
        normalized[name] = vector
    for required in ("candidate", "kalshi_prior", "structural", "logistic"):
        if required not in normalized:
            raise EvaluationError(f"lockbox prediction map lacks required component {required}")
    normalized.setdefault("naive_50", [0.5] * expected)
    return normalized


def _predict_bundle_rows(bundle: artifact.ModelBundle, rows: Sequence[FeatureRow]) -> dict[str, list[float]]:
    """Run only a fully hash-verified locally generated serialized pipeline."""
    try:
        payload = artifact.serialized_pipeline_bytes(bundle)
        pipeline = pickle.loads(payload)
    except Exception as exc:
        raise EvaluationError("verified model pipeline could not be deserialized") from exc
    if not isinstance(pipeline, training.FittedBootstrapPipeline):
        raise EvaluationError("serialized model payload has unexpected pipeline type")
    try:
        return pipeline.predict(rows)
    except Exception as exc:
        raise EvaluationError("serialized model pipeline failed lockbox inference") from exc


def evaluate_lockbox(bundle: artifact.ModelBundle, dataset: Sequence[FeatureRow]) -> BootstrapEvaluationReport:
    """Evaluate exactly the predeclared, causally evaluable lockbox scope."""
    if bundle.stage != "experiment":
        raise EvaluationError("lockbox evaluation accepts experiment bundles only")
    if not bundle.leakage_audit.passed:
        raise EvaluationError("experiment development leakage audit did not pass")
    rows = tuple(dataset)
    if not rows:
        raise EvaluationError("lockbox dataset cannot be empty")

    boundaries = bundle.boundaries
    selected = tuple(
        row
        for row in rows
        if boundaries.lockbox_start_ts_ns <= row.checkpoint_ts_ns <= boundaries.lockbox_end_ts_ns
        and training.is_evaluation_row(row)
    )
    if not selected:
        raise EvaluationError("no rows fall inside the predeclared lockbox evaluation scope")

    required_features = set(bundle.feature_names)
    schema_match = all(required_features.issubset(row.features) for row in selected)
    if not schema_match:
        raise EvaluationError("lockbox feature schema does not satisfy the trained feature manifest")

    leakage = audit_dataset_rows(selected)
    leakage_evidence = artifact.LeakageEvidence(
        passed=leakage.passed,
        finding_count=leakage.finding_count,
        finding_codes=tuple(sorted({finding.code for finding in leakage.findings})),
    )
    if not leakage.passed:
        raise EvaluationError("lockbox leakage audit failed")

    raw_predictions = _predict_bundle_rows(bundle, selected)
    predictions = _validate_prediction_map(raw_predictions, len(selected))
    labels = [row.label_yes for row in selected]
    snapshots = {
        name: artifact.metric_snapshot(probability_metrics(labels, vector))
        for name, vector in predictions.items()
    }
    ablation_names = {item.name for item in bundle.ablations}
    return BootstrapEvaluationReport(
        bundle_sha256=bundle.bundle_sha256 or artifact.bundle_sha256(bundle),
        dataset_sha256=_dataset_hash(bundle),
        model_version=bundle.model_version,
        lockbox_start_ts_ns=boundaries.lockbox_start_ts_ns,
        lockbox_end_ts_ns=boundaries.lockbox_end_ts_ns,
        evaluated_rows=len(selected),
        excluded_rows=len(rows) - len(selected),
        feature_schema_match=True,
        leakage_audit=leakage_evidence,
        required_ablations_present=set(REQUIRED_ABLATIONS).issubset(ablation_names),
        metrics=snapshots,
        comparison_rule=PROMOTION_RULE_ID,
    )


def _atomic_json(path: Path, payload: Mapping[str, object]) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _resolved_git_sha(explicit: str | None = None) -> str:
    value = explicit or os.environ.get("GITHUB_SHA") or os.environ.get("BUILD_GIT_SHA")
    if value is None:
        try:
            value = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise EvaluationError("cannot resolve Git SHA for reproducible model bundle") from exc
    value = value.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise EvaluationError("Git SHA must be a 40-character hexadecimal commit SHA")
    return value


def _build_experiment_bundle(root: Path, settings: BootstrapSettings, *, git_sha: str) -> artifact.ModelBundle:
    return training.build_experiment_bundle(root, settings, git_sha=git_sha)


def train_experiment(
    root: Path,
    settings: BootstrapSettings,
    *,
    git_sha: str | None = None,
) -> BootstrapTrainingReport:
    """Train and save an experiment bundle without reading/evaluating the lockbox."""
    root = root.resolve()
    resolved_sha = _resolved_git_sha(git_sha)
    bundle = _build_experiment_bundle(root, settings, git_sha=resolved_sha)
    if bundle.stage != "experiment":
        raise EvaluationError("training must produce an experiment bundle")
    experiment_path = artifact.save_model_bundle(bundle, root / "models" / "experiments")
    sealed = artifact.load_model_bundle(experiment_path)
    train_rows, calibration_rows, lockbox_rows = training.partition_row_counts(root, sealed.boundaries)
    report_path = root / "reports" / f"training-{experiment_path.stem}.json"
    report = BootstrapTrainingReport(
        experiment_bundle_sha256=experiment_path.stem,
        experiment_path=experiment_path,
        report_path=report_path,
        train_rows=train_rows,
        calibration_rows=calibration_rows,
        lockbox_rows=lockbox_rows,
        excluded_feature_families=sealed.excluded_feature_families,
        excluded_components=sealed.excluded_components,
    )
    _atomic_json(report_path, report.model_dump(mode="json"))
    _atomic_json(
        root / "models" / "experiments" / "latest.json",
        {
            "bundle_sha256": experiment_path.stem,
            "path": experiment_path.relative_to(root).as_posix(),
        },
    )
    return report


def finalize_evaluation(
    root: Path,
    experiment_path: Path,
    report: BootstrapEvaluationReport,
) -> BootstrapEvaluationRun:
    """Persist the one-time evaluation and promote only a passing experiment."""
    root = root.resolve()
    experiment_path = experiment_path.resolve()
    experiment = artifact.load_model_bundle(experiment_path)
    if experiment.stage != "experiment":
        raise EvaluationError("only experiment bundles can be finalized")
    if report.bundle_sha256 != experiment.bundle_sha256:
        raise EvaluationError("evaluation report bundle hash does not match experiment")
    report_path = root / "reports" / f"evaluation-{experiment.bundle_sha256}.json"
    if report_path.exists():
        raise EvaluationError("this experiment has already been evaluated against the lockbox")

    decision = promotion_decision(report)
    _atomic_json(
        report_path,
        {
            "report": report.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        },
    )
    if not decision.promoted:
        return BootstrapEvaluationRun(
            experiment_path=experiment_path,
            report_path=report_path,
            decision=decision,
            promoted_path=None,
            default_path=None,
        )

    promoted_metrics = {
        **{f"development_{name}": value for name, value in experiment.metrics.items()},
        **{f"lockbox_{name}": value for name, value in report.metrics.items()},
    }
    promoted = experiment.model_copy(
        update={
            "stage": "promoted",
            "metrics": promoted_metrics,
            "source_experiment_sha256": experiment.bundle_sha256,
            "promotion_rule": decision.comparison_rule,
            "promotion_reason_codes": decision.reason_codes,
            "bundle_sha256": None,
        }
    )
    promoted_path = artifact.save_model_bundle(promoted, root / "models" / "promoted")
    sealed_promoted = artifact.load_model_bundle(promoted_path)
    default_path = _atomic_json(
        root / "models" / "default.json",
        {
            "bundle_sha256": sealed_promoted.bundle_sha256,
            "path": promoted_path.relative_to(root).as_posix(),
            "source_experiment_sha256": experiment.bundle_sha256,
            "promotion_rule": decision.comparison_rule,
        },
    )
    return BootstrapEvaluationRun(
        experiment_path=experiment_path,
        report_path=report_path,
        decision=decision,
        promoted_path=promoted_path,
        default_path=default_path,
    )


def _latest_experiment_path(root: Path) -> Path:
    pointer_path = root.resolve() / "models" / "experiments" / "latest.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        relative = str(pointer["path"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise EvaluationError("no valid latest experiment pointer exists") from exc
    path = (root.resolve() / relative).resolve()
    expected_root = (root.resolve() / "models" / "experiments").resolve()
    if expected_root not in path.parents:
        raise EvaluationError("latest experiment pointer escapes experiment directory")
    return path


def run_lockbox_evaluation(root: Path, bundle_path: Path | None = None) -> BootstrapEvaluationRun:
    """Materialize the lockbox only after confirming the experiment has not been evaluated."""
    root = root.resolve()
    experiment_path = _latest_experiment_path(root) if bundle_path is None else bundle_path.resolve()
    experiment = artifact.load_model_bundle(experiment_path)
    if experiment.bundle_sha256 is None:
        raise EvaluationError("experiment bundle lacks content hash")
    report_path = root / "reports" / f"evaluation-{experiment.bundle_sha256}.json"
    if report_path.exists():
        raise EvaluationError("this experiment has already been evaluated against the lockbox")
    try:
        lockbox_rows = training.load_lockbox_rows(root, experiment.boundaries)
    except Exception as exc:
        raise EvaluationError("could not materialize the predeclared lockbox") from exc
    report = evaluate_lockbox(experiment, lockbox_rows)
    return finalize_evaluation(root, experiment_path, report)
