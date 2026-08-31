from __future__ import annotations

"""Untouched lockbox evaluation, development ablations, and promotion policy."""

import math
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from . import artifact
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
    matches = [digest for path, digest in bundle.input_hashes.items() if path.endswith("derived/features.parquet") or path == "derived/features.parquet"]
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
    for required in ("candidate", "kalshi_prior"):
        if required not in normalized:
            raise EvaluationError(f"lockbox prediction map lacks required component {required}")
    normalized.setdefault("naive_50", [0.5] * expected)
    return normalized


def _predict_bundle_rows(bundle: artifact.ModelBundle, rows: Sequence[FeatureRow]) -> dict[str, list[float]]:
    """Task 8 training slice replaces this guard with verified serialized-pipeline inference."""
    raise EvaluationError("serialized bootstrap pipeline inference is not wired yet")


def evaluate_lockbox(bundle: artifact.ModelBundle, dataset: Sequence[FeatureRow]) -> BootstrapEvaluationReport:
    """Evaluate exactly the predeclared lockbox block of an experiment bundle."""
    if bundle.stage != "experiment":
        raise EvaluationError("lockbox evaluation accepts experiment bundles only")
    rows = tuple(dataset)
    if not rows:
        raise EvaluationError("lockbox dataset cannot be empty")

    boundaries = bundle.boundaries
    selected = tuple(
        row
        for row in rows
        if boundaries.lockbox_start_ts_ns <= row.checkpoint_ts_ns <= boundaries.lockbox_end_ts_ns
    )
    if not selected:
        raise EvaluationError("no rows fall inside the predeclared lockbox boundaries")

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
