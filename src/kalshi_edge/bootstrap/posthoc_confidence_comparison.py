"""Read-only, explicitly post-hoc 60% vs 69% Model 1 / Model 2 comparison.

Reconstructs the old, already-evaluated 480-second cohort ONLY for the user's
descriptive threshold question; never updates automatic promotion evidence.

Original one-time report does not save raw probabilities, so exact threshold
recalculation necessarily requires model inference. This replay is NOT a new
independent validation and must NOT influence model training/promotion.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from . import artifact, evaluate, live, training
from .provenance import verify_artifact


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"required original model metadata unavailable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("model pointer or original evaluation report is not a JSON object")
    return value


def _sha(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value.lower())):
        raise ValueError("original model SHA-256 must be a full 64-character hex digest")
    return value.lower()


def resolve_model_one(root: Path) -> artifact.ModelBundle:
    """Find the ORIGINAL model via verified numbered active/previous pointer."""
    root = Path(root).resolve()
    pointer = _load_json(root / "models" / "default.json")
    n = pointer.get("model_number", 1)
    if type(n) is not int:
        raise ValueError("Model 1 pointer has invalid numbering")
    if n == 1:
        return live.load_default_bundle(root)
    if n != 2:
        raise ValueError("Cannot establish Model 1 identity from the current model pointer")
    previous_sha = _sha(pointer.get("previous_model_hash"))
    previous_path = (root / "models" / "promoted" / f"{previous_sha}.json").resolve()
    bundle = artifact.load_model_bundle(previous_path)
    if (bundle.bundle_sha256 != previous_sha
            or bundle.stage != "promoted"
            or bundle.promotion_rule != evaluate.PROMOTION_RULE_ID):
        raise ValueError("previous Model 1 bundle is missing, altered or not an original promotion")
    return bundle


def validate_report_identity(
    experiment: artifact.ModelBundle,
    report: evaluate.BootstrapEvaluationReport,
) -> None:
    """Require exact original model/data/timing provenance before rereading labels."""
    if (report.bundle_sha256 != experiment.bundle_sha256
            or report.dataset_sha256 != experiment.input_hashes.get("derived/features.parquet")
            or report.model_version != experiment.model_version
            or report.lockbox_start_ts_ns != experiment.boundaries.lockbox_start_ts_ns
            or report.lockbox_end_ts_ns != experiment.boundaries.lockbox_end_ts_ns):
        raise ValueError("original saved evaluation report / experiment provenance identity mismatch")
    if (not report.requires_retrain_480_gate
            or "retrain_480_candidate" not in report.decision_metrics
            or "retrain_480_incumbent" not in report.decision_metrics):
        raise ValueError("original full-retrain 480s gate is unavailable")


def _validated_saved_report(root: Path):
    state = _load_json(root / "training_runs" / "current.json")
    if (state.get("stage") != "COMPLETE" or state.get("mode") != "baseline"
            or state.get("decision") not in ("VALIDATION_FAILED", "PROMOTED")):
        raise ValueError("the original completed baseline retrain must be the current saved run")
    digest = _sha(state.get("experiment_sha256"))
    report_path = (root / "reports" / f"evaluation-{digest}.json").resolve()
    if (not isinstance(state.get("evaluation_report"), str)
            or Path(state["evaluation_report"]).resolve() != report_path):
        raise ValueError("original evaluation report path does not match the completed run")
    if not report_path.is_file():
        raise ValueError("original evaluation report was not preserved")
    experiment_path = root / "models" / "experiments" / f"{digest}.json"
    experiment = artifact.load_model_bundle(experiment_path)
    if (experiment.stage != "experiment" or experiment.bundle_sha256 != digest):
        raise ValueError("original Model 2 experiment hash or stage invalid")
    if (experiment.model_version != training.MODEL_VERSION
            or experiment.feature_schema_version != training.FEATURE_SCHEMA_VERSION):
        raise ValueError("original Model 2 bundle requires an incompatible code/model version")
    payload = _load_json(report_path)
    try:
        report = evaluate.BootstrapEvaluationReport.model_validate(payload["report"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("original evaluation report cannot be validated") from exc
    validate_report_identity(experiment, report)
    audit = report.paired_480_audit
    if (not isinstance(audit, dict) or audit.get("market_count", 0) < 80
            or audit.get("minimum_confidence") != .69):
        raise ValueError("original paired 69% lockbox audit missing or inconsistent")
    if not report.leakage_audit.passed or not report.feature_schema_match:
        raise ValueError("original lockbox integrity evidence failed")
    return experiment, report


def _historical_480_rows(
    root: Path,
    experiment: artifact.ModelBundle,
    report: evaluate.BootstrapEvaluationReport,
):
    """Read ONLY already-consumed original 480s interval after hash check."""
    import pyarrow.parquet as pq

    dataset_path, manifest_path, provenance_path = training._dataset_paths(root)
    if not verify_artifact(dataset_path, manifest_path):
        raise ValueError("historical dataset manifest checksum failed; no replay")
    manifest = _load_json(manifest_path)
    provenance = _load_json(provenance_path)
    original_sha = report.dataset_sha256
    if (manifest.get("sha256") != original_sha
            or provenance.get("dataset_sha256") != original_sha):
        raise ValueError(
            "historical dataset SHA changed since the original lockbox; "
            "cannot reproduce the 1,222-market comparison"
        )
    features = manifest.get("metadata", {}).get("feature_names")
    if (not isinstance(features, list) or not features
            or not all(isinstance(f, str) for f in features)):
        raise ValueError("verified historical feature-name manifest missing")
    schema = pq.read_schema(dataset_path)
    names = (
        "market_ticker", "market_date", "split_group_id",
        "checkpoint_ts_ns", "label_yes", *features,
        *(name for name in schema.names if name.startswith("source_ts__")),
    )
    if len(set(names)) != len(names) or not set(names).issubset(schema.names):
        raise ValueError("historical feature schema does not match original source")
    table = pq.read_table(
        dataset_path, columns=list(names),
        filters=[
            ("checkpoint_ts_ns", ">=", experiment.boundaries.lockbox_start_ts_ns),
            ("checkpoint_ts_ns", "<=", experiment.boundaries.lockbox_end_ts_ns),
        ],
    )
    all_rows = training._table_to_rows(table, features)
    evaluable = [
        row for row in all_rows
        if training.is_evaluation_row(row)
        and experiment.boundaries.lockbox_start_ts_ns <= row.checkpoint_ts_ns
        <= experiment.boundaries.lockbox_end_ts_ns
    ]
    selected = evaluate._one_checkpoint_per_market(evaluable, 480)
    count = report.decision_metrics["retrain_480_candidate"].total_markets
    if len(selected) != count or len(selected) != report.paired_480_audit["market_count"]:
        raise ValueError("original 480s market population count changed; comparison aborted")
    if not evaluate.audit_dataset_rows(selected).passed:
        raise ValueError("causal data/source timestamp integrity failed")
    return selected


def _gate_view(gate: evaluate.GateSnapshot) -> dict[str, Any]:
    return {
        "qualified": gate.qualified_markets,
        "correct": gate.qualified_correct,
        "incorrect": gate.qualified_markets - gate.qualified_correct,
        "skipped": gate.total_markets - gate.qualified_markets,
        "coverage": gate.coverage,
        "qualified_accuracy": gate.qualified_accuracy,
    }


def compare_probabilities(
    labels: Sequence[int],
    model_1_probabilities: Sequence[float],
    model_2_probabilities: Sequence[float],
    original_69_gates: dict[str, evaluate.GateSnapshot],
) -> dict[str, Any]:
    """Strictly descriptive 60% and 69% counts on identical market order."""
    old_1 = original_69_gates.get("retrain_480_incumbent")
    old_2 = original_69_gates.get("retrain_480_candidate")
    if old_1 is None or old_2 is None:
        raise ValueError("original 69% checkpoint-gate counts missing")
    m1_69 = evaluate.checkpoint_gate_snapshot(labels, model_1_probabilities, minimum_confidence=.69)
    m2_69 = evaluate.checkpoint_gate_snapshot(labels, model_2_probabilities, minimum_confidence=.69)
    for source, new in ((old_1, m1_69), (old_2, m2_69)):
        if (
            source.minimum_confidence != .69
            or source.total_markets != new.total_markets
            or source.qualified_markets != new.qualified_markets
            or source.qualified_correct != new.qualified_correct
        ):
            raise ValueError(
                "Recomputed 69% counts mismatch the immutable original report. "
                "Do not present 60% results from a changed model or cohort."
            )
    m1_60 = evaluate.checkpoint_gate_snapshot(labels, model_1_probabilities, minimum_confidence=.60)
    m2_60 = evaluate.checkpoint_gate_snapshot(labels, model_2_probabilities, minimum_confidence=.60)
    n = len(labels)
    def qualified(p: float) -> bool:
        return 2 * abs(p - .5) >= .60
    q1 = [qualified(p) for p in model_1_probabilities]
    q2 = [qualified(p) for p in model_2_probabilities]
    return {
        "status": "DESCRIPTIVE_POSTHOC_REPLAY",
        "market_count": n,
        "checkpoint_seconds_remaining": 480,
        "thresholds": [0.60, 0.69],
        "original_69_gate_verified": True,
        "automatic_promotion_eligible": False,
        "live_confidence_threshold_changed": False,
        "model_1": {
            "at_69": _gate_view(m1_69),
            "at_60": _gate_view(m1_60),
            "qualified_added_at_60": m1_60.qualified_markets - m1_69.qualified_markets,
            "all_markets_direction_accuracy": sum(
                (p >= .5) == bool(y) for p,y in zip(model_1_probabilities, labels)
            ) / n,
        },
        "model_2": {
            "at_69": _gate_view(m2_69),
            "at_60": _gate_view(m2_60),
            "qualified_added_at_60": m2_60.qualified_markets - m2_69.qualified_markets,
            "all_markets_direction_accuracy": sum(
                (p >= .5) == bool(y) for p,y in zip(model_2_probabilities, labels)
            ) / n,
        },
        "matched_60": {
            "both_qualified": sum(a and b for a,b in zip(q1,q2)),
            "model_1_only_qualified": sum(a and not b for a,b in zip(q1,q2)),
            "model_2_only_qualified": sum(b and not a for a,b in zip(q1,q2)),
            "neither_qualified": sum(not a and not b for a,b in zip(q1,q2)),
        },
        "limitations": (
            "This threshold was examined after viewing the original lockbox outcomes. "
            "It is a post-hoc descriptive replay, NOT a new unseen validation, "
            "automatic promotion approval or realized-profit comparison."
        ),
    }


def run_comparison(root: Path, *, acknowledge_posthoc: bool) -> dict[str, Any]:
    """Perform no writes. Explicit acknowledgement BEFORE touching archived labels."""
    if acknowledge_posthoc is not True:
        raise ValueError(
            "post-hoc lockbox replay requires --acknowledge-posthoc; "
            "cannot treat this as fresh or independent evidence"
        )
    root = Path(root).resolve()
    experiment, report = _validated_saved_report(root)
    original = resolve_model_one(root)
    if (original.model_version != experiment.model_version
            or original.feature_schema_version != experiment.feature_schema_version):
        raise ValueError("Model 1 and Model 2 version/schema mismatch")
    rows = _historical_480_rows(root, experiment, report)
    if not set(original.feature_names).issubset(rows[0].features):
        raise ValueError("Model 1 historical feature schema mismatch")
    if not set(experiment.feature_names).issubset(rows[0].features):
        raise ValueError("Model 2 historical feature schema mismatch")
    print(f"Post-hoc diagnostic: computing Model 1 / Model 2 for {len(rows)} archived markets",
          file=sys.stderr, flush=True)
    gpu_backends: dict[str, object | None] = {}
    with evaluate._holdout_executor() as executor:
        old_predictions = evaluate._predict_bundle_candidate(
            original, rows, structural_executor=executor, gpu_backends=gpu_backends,
            structural_progress=lambda done,total: print(
                f"Model 1: {done}/{total} structural rows",file=sys.stderr,flush=True
            ),
        )
        new_predictions = evaluate._predict_bundle_candidate(
            experiment, rows, structural_executor=executor, gpu_backends=gpu_backends,
            structural_progress=lambda done,total: print(
                f"Model 2: {done}/{total} structural rows",file=sys.stderr,flush=True
            ),
        )
    result = compare_probabilities(
        [row.label_yes for row in rows],
        old_predictions, new_predictions, report.decision_metrics,
    )
    result["model_1_bundle_sha256"] = original.bundle_sha256
    result["model_2_experiment_sha256"] = experiment.bundle_sha256
    result["original_evaluation_rule"] = report.comparison_rule
    result["saved_report_modified"] = False
    result["model_pointer_modified"] = False
    return result


def main(argv: Sequence[str] | None = None) -> int:
    from .config import BootstrapSettings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--acknowledge-posthoc", action="store_true",
                        help="Accept replay is descriptive only; not an untouched holdout")
    args = parser.parse_args(argv)
    result = run_comparison(args.root,acknowledge_posthoc=args.acknowledge_posthoc)
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
