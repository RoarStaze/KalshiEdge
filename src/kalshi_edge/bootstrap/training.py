from __future__ import annotations

"""Development-only bootstrap training pipeline.

The training entrypoint deliberately performs a metadata-only scan of the full
feature matrix to predeclare chronological partitions. It then materializes only
rows strictly before the lockbox boundary. Lockbox labels/features are loaded by
the evaluation path only.
"""

import base64
import hashlib
import importlib.metadata
import json
import math
import pickle
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from . import artifact, calibration, models, structural
from .config import BootstrapSettings
from .leakage import audit_dataset_rows
from .metrics import ProbabilityMetrics, probability_metrics
from .provenance import verify_artifact
from .splits import MarketIndex, WalkForwardSplit, make_walk_forward_splits
from .types import FeatureRow


MODEL_VERSION = "bootstrap-hybrid-v1"
FEATURE_SCHEMA_VERSION = 1
STRUCTURAL_SIMULATIONS = 512
EVALUATION_CHECKPOINT_SECONDS = (840.0, 600.0, 300.0, 120.0, 60.0)
KALSHI_FLOW_PREFIXES = (
    "kalshi_trade_volume_",
    "kalshi_yes_taker_imbalance_",
    "kalshi_trade_return_",
)
STRUCTURAL_FEATURES = (
    "btc_close",
    "strike",
    "seconds_remaining",
    "btc_realized_vol_60s",
    "btc_return_5s",
    "btc_return_5s_available",
)
PRIOR_FEATURES = (
    "kalshi_mid",
    "kalshi_mid_available",
    "kalshi_last_trade_yes",
    "kalshi_last_trade_available",
)


class TrainingError(RuntimeError):
    """Raised when a leakage-safe experiment cannot be trained."""


@dataclass(frozen=True)
class PartitionPlan:
    development_groups: tuple[str, ...]
    calibration_groups: tuple[str, ...]
    lockbox_groups: tuple[str, ...]
    boundaries: artifact.SplitBoundaries


@dataclass(frozen=True)
class DatasetContext:
    dataset_path: Path
    manifest_path: Path
    provenance_path: Path
    feature_names: tuple[str, ...]
    input_hashes: dict[str, str]
    plan: PartitionPlan


@dataclass(frozen=True)
class FittedBootstrapPipeline:
    """Serializable fitted pipeline consumed by Task 8 evaluation and Task 9 live inference."""

    outcome_feature_names: tuple[str, ...]
    required_feature_names: tuple[str, ...]
    outcome_model_name: str
    outcome_model: object
    logistic_model: object
    structural_model: structural.StructuralModel
    residual_model: models.ResidualModel
    stacker: models.Stacker
    calibrator: calibration.Calibrator

    def predict(self, rows: Sequence[FeatureRow]) -> dict[str, list[float]]:
        selected = tuple(rows)
        if not selected:
            raise TrainingError("prediction requires at least one row")
        _require_feature_schema(selected, self.required_feature_names)
        if any(not is_evaluation_row(row) for row in selected):
            raise TrainingError("historical pipeline prediction requires a predeclared evaluation checkpoint")

        x = _matrix(selected, self.outcome_feature_names)
        prior = [_kalshi_prior(row) for row in selected]
        historical = _predict_classifier(self.outcome_model, x)
        logistic = _predict_classifier(self.logistic_model, x)
        structural_p = [
            self.structural_model.predict_proba(structural.structural_state_from_row(row))
            for row in selected
        ]
        residual_p = self.residual_model.predict(x, prior)
        component_map = {
            "kalshi_prior": prior,
            "historical_ml": historical,
            "structural": structural_p,
            "residual_corrected": residual_p,
        }
        active = {
            name: values
            for name, values in component_map.items()
            if self.stacker.weights.get(name, 0.0) > 0.0
        }
        raw = self.stacker.predict(active)
        candidate = self.calibrator.predict(raw)
        return {
            "candidate": candidate,
            "kalshi_prior": prior,
            "structural": structural_p,
            "logistic": logistic,
            "naive_50": [0.5] * len(selected),
            "historical_ml": historical,
            "residual_corrected": residual_p,
        }


def is_evaluation_row(row: FeatureRow) -> bool:
    seconds = row.features.get("seconds_remaining")
    if seconds is None or not math.isfinite(seconds):
        return False
    return any(abs(seconds - checkpoint) <= 1e-9 for checkpoint in EVALUATION_CHECKPOINT_SECONDS)


def _dataset_paths(root: Path) -> tuple[Path, Path, Path]:
    root = root.resolve()
    return (
        root / "derived" / "features.parquet",
        root / "manifests" / "dataset" / "features.parquet.manifest.json",
        root / "derived" / "features.provenance.json",
    )


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"cannot read verified training metadata: {path}") from exc
    if not isinstance(value, dict):
        raise TrainingError(f"training metadata must be a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_context(root: Path) -> DatasetContext:
    dataset_path, manifest_path, provenance_path = _dataset_paths(root)
    try:
        manifest = verify_artifact(dataset_path, manifest_path)
    except Exception as exc:  # provenance module exposes domain-specific integrity errors
        raise TrainingError("feature dataset failed manifest verification") from exc
    metadata = dict(manifest.metadata or {})
    feature_names = tuple(str(value) for value in metadata.get("feature_names", ()))
    if not feature_names:
        raise TrainingError("dataset manifest lacks exact feature-name order")

    provenance = _read_json(provenance_path)
    if str(provenance.get("dataset_sha256", "")).lower() != manifest.sha256.lower():
        raise TrainingError("dataset provenance hash does not match verified parquet")
    inputs = provenance.get("inputs")
    if not isinstance(inputs, list):
        raise TrainingError("dataset provenance lacks input hash list")
    input_hashes: dict[str, str] = {"derived/features.parquet": manifest.sha256.lower()}
    for item in inputs:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise TrainingError("dataset provenance contains malformed input hash")
        input_hashes[str(item["path"])] = str(item["sha256"]).lower()
    input_hashes["derived/features.provenance.json"] = _sha256_file(provenance_path)
    input_hashes["manifests/dataset/features.parquet.manifest.json"] = _sha256_file(manifest_path)

    plan = _partition_plan(dataset_path)
    return DatasetContext(
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        provenance_path=provenance_path,
        feature_names=feature_names,
        input_hashes=input_hashes,
        plan=plan,
    )


def _metadata_groups(dataset_path: Path) -> list[tuple[str, int, int]]:
    import pyarrow.parquet as pq

    table = pq.read_table(
        dataset_path,
        columns=["market_ticker", "split_group_id", "checkpoint_ts_ns"],
    )
    data = table.to_pydict()
    groups: dict[str, tuple[int, int]] = {}
    for ticker, group_id, ts in zip(data["market_ticker"], data["split_group_id"], data["checkpoint_ts_ns"]):
        group = str(group_id or ticker)
        timestamp = int(ts)
        if group not in groups:
            groups[group] = (timestamp, timestamp)
        else:
            low, high = groups[group]
            groups[group] = (min(low, timestamp), max(high, timestamp))
    ordered = sorted(((group, low, high) for group, (low, high) in groups.items()), key=lambda item: item[1])
    if len(ordered) < 12:
        raise TrainingError("at least 12 chronological markets are required for train/calibration/lockbox separation")
    if any(current[1] <= previous[1] for previous, current in zip(ordered, ordered[1:])):
        raise TrainingError("market groups are not strictly chronological")
    return ordered


def _partition_plan(dataset_path: Path) -> PartitionPlan:
    ordered = _metadata_groups(dataset_path)
    count = len(ordered)
    lockbox_count = max(2, count // 10)
    calibration_count = max(2, count // 10)
    development_count = count - lockbox_count - calibration_count
    if development_count < 8:
        raise TrainingError("insufficient development markets after reserving calibration and lockbox")

    development = ordered[:development_count]
    calibration_groups = ordered[development_count : development_count + calibration_count]
    lockbox = ordered[development_count + calibration_count :]
    boundaries = artifact.SplitBoundaries(
        train_start_ts_ns=min(item[1] for item in development),
        train_end_ts_ns=max(item[2] for item in development),
        calibration_start_ts_ns=min(item[1] for item in calibration_groups),
        calibration_end_ts_ns=max(item[2] for item in calibration_groups),
        lockbox_start_ts_ns=min(item[1] for item in lockbox),
        lockbox_end_ts_ns=max(item[2] for item in lockbox),
    )
    return PartitionPlan(
        development_groups=tuple(item[0] for item in development),
        calibration_groups=tuple(item[0] for item in calibration_groups),
        lockbox_groups=tuple(item[0] for item in lockbox),
        boundaries=boundaries,
    )


def _table_to_rows(table, feature_names: Sequence[str]) -> tuple[FeatureRow, ...]:
    data = table.to_pydict()
    source_columns = [name for name in table.column_names if name.startswith("source_ts__")]
    rows: list[FeatureRow] = []
    for index in range(table.num_rows):
        features: dict[str, float] = {}
        for name in feature_names:
            value = data[name][index]
            features[name] = float(value) if value is not None and math.isfinite(float(value)) else 0.0
        sources = {
            name.removeprefix("source_ts__"): int(data[name][index])
            for name in source_columns
            if data[name][index] is not None
        }
        rows.append(
            FeatureRow(
                market_ticker=str(data["market_ticker"][index]),
                market_date=None if data["market_date"][index] is None else str(data["market_date"][index]),
                split_group_id=None if data["split_group_id"][index] is None else str(data["split_group_id"][index]),
                checkpoint_ts_ns=int(data["checkpoint_ts_ns"][index]),
                label_yes=int(data["label_yes"][index]),
                features=features,
                source_max_ts_ns=sources,
            )
        )
    return tuple(rows)


def _read_rows_before_lockbox(context: DatasetContext) -> tuple[FeatureRow, ...]:
    import pyarrow.parquet as pq

    columns = [
        "market_ticker",
        "market_date",
        "split_group_id",
        "checkpoint_ts_ns",
        "label_yes",
        *context.feature_names,
    ]
    schema = pq.read_schema(context.dataset_path)
    columns.extend(name for name in schema.names if name.startswith("source_ts__"))
    table = pq.read_table(
        context.dataset_path,
        columns=columns,
        filters=[("checkpoint_ts_ns", "<", context.plan.boundaries.lockbox_start_ts_ns)],
    )
    rows = _table_to_rows(table, context.feature_names)
    if any(row.checkpoint_ts_ns >= context.plan.boundaries.lockbox_start_ts_ns for row in rows):
        raise TrainingError("training loader materialized a lockbox row")
    return rows


def load_lockbox_rows(root: Path, boundaries: artifact.SplitBoundaries) -> tuple[FeatureRow, ...]:
    """Materialize only predeclared lockbox rows for the evaluation entrypoint."""
    context = _verified_context(root)
    if context.plan.boundaries != boundaries:
        raise TrainingError("current dataset partition boundaries do not match experiment bundle")
    import pyarrow.parquet as pq

    columns = [
        "market_ticker",
        "market_date",
        "split_group_id",
        "checkpoint_ts_ns",
        "label_yes",
        *context.feature_names,
    ]
    schema = pq.read_schema(context.dataset_path)
    columns.extend(name for name in schema.names if name.startswith("source_ts__"))
    table = pq.read_table(
        context.dataset_path,
        columns=columns,
        filters=[
            ("checkpoint_ts_ns", ">=", boundaries.lockbox_start_ts_ns),
            ("checkpoint_ts_ns", "<=", boundaries.lockbox_end_ts_ns),
        ],
    )
    return _table_to_rows(table, context.feature_names)


def _group(row: FeatureRow) -> str:
    return row.split_group_id or row.market_ticker


def _rows_for_groups(rows: Sequence[FeatureRow], groups: Sequence[str]) -> tuple[FeatureRow, ...]:
    wanted = set(groups)
    return tuple(row for row in rows if _group(row) in wanted)


def _development_splits(rows: Sequence[FeatureRow], groups: Sequence[str]) -> list[WalkForwardSplit]:
    first_ts: dict[str, int] = {}
    for row in rows:
        group = _group(row)
        first_ts[group] = min(first_ts.get(group, row.checkpoint_ts_ns), row.checkpoint_ts_ns)
    markets = tuple(
        MarketIndex(market_ticker=group, split_group_id=group, first_checkpoint_ts_ns=first_ts[group])
        for group in groups
    )
    count = len(markets)
    validation_markets = max(1, count // 6)
    min_train = max(4, count // 2)
    return make_walk_forward_splits(
        markets,
        min_train_markets=min_train,
        validation_markets=validation_markets,
        embargo_markets=1,
    )


def _filtered_rows(rows: Sequence[FeatureRow], *, remove_btc: bool = False, remove_kalshi_flow: bool = False) -> tuple[FeatureRow, ...]:
    output: list[FeatureRow] = []
    for row in rows:
        features = {
            name: value
            for name, value in row.features.items()
            if not (remove_btc and name.startswith("btc_"))
            and not (remove_kalshi_flow and name.startswith(KALSHI_FLOW_PREFIXES))
        }
        output.append(row.model_copy(update={"features": features}))
    return tuple(output)


def _later_metrics(result: models.CandidateResults, rows: Sequence[FeatureRow], candidate: str | None = None) -> ProbabilityMetrics:
    name = candidate or result.best_candidate
    predictions = result.oof_predictions[name]
    positions = [index for index, value in enumerate(predictions) if value is not None]
    if not positions:
        raise TrainingError("candidate produced no development OOF predictions")
    positions.sort(key=lambda index: rows[index].checkpoint_ts_ns)
    later_count = max(1, len(positions) // 3)
    positions = positions[-later_count:]
    return probability_metrics(
        [rows[index].label_yes for index in positions],
        [float(predictions[index]) for index in positions],
    )


def _matrix(rows: Sequence[FeatureRow], feature_names: Sequence[str]) -> list[list[float]]:
    matrix: list[list[float]] = []
    for row in rows:
        matrix.append([float(row.features.get(name, 0.0)) for name in feature_names])
    if not matrix or not feature_names:
        raise TrainingError("model matrix cannot be empty")
    return matrix


def _predict_classifier(model: object, matrix: Sequence[Sequence[float]]) -> list[float]:
    raw = model.predict_proba(matrix)[:, 1]
    return [min(1.0, max(0.0, float(value))) for value in raw]


def _kalshi_prior(row: FeatureRow) -> float:
    features = row.features
    if features.get("kalshi_mid_available", 0.0) > 0.0:
        value = features.get("kalshi_mid")
        if value is not None and math.isfinite(value) and 0.0 <= value <= 1.0:
            return float(value)
    if features.get("kalshi_last_trade_available", 0.0) > 0.0:
        value = features.get("kalshi_last_trade_yes")
        if value is not None and math.isfinite(value) and 0.0 <= value <= 1.0:
            return float(value)
    return 0.5


def _require_feature_schema(rows: Sequence[FeatureRow], names: Sequence[str]) -> None:
    required = set(names)
    for row in rows:
        if not required.issubset(row.features):
            missing = sorted(required - set(row.features))
            raise TrainingError(f"row {row.market_ticker} is missing trained feature {missing[0]}")


def _fold_group_rows(rows: Sequence[FeatureRow], group_order: Sequence[str], indices: Sequence[int]) -> tuple[FeatureRow, ...]:
    groups = [group_order[index] for index in indices]
    return _rows_for_groups(rows, groups)


def _structural_training(
    development_rows: Sequence[FeatureRow],
    development_groups: Sequence[str],
    splits: Sequence[WalkForwardSplit],
    *,
    seed: int,
) -> tuple[structural.StructuralModel, dict[int, float], str]:
    first_fold = splits[0]
    selector_groups = tuple(first_fold.train_indices)
    if len(selector_groups) < 4:
        raise TrainingError("structural selection requires at least four early development groups")
    pivot = max(2, len(selector_groups) * 2 // 3)
    if pivot >= len(selector_groups):
        pivot = len(selector_groups) - 1
    selector_train = _fold_group_rows(development_rows, development_groups, selector_groups[:pivot])
    selector_validation = _fold_group_rows(development_rows, development_groups, selector_groups[pivot:])
    selection = structural.select_structural_model(
        selector_train,
        selector_validation,
        seed=seed,
        simulations=STRUCTURAL_SIMULATIONS,
    )
    candidate = selection.evidence.chosen_candidate

    oof: dict[int, float] = {}
    group_to_position = {group: position for position, group in enumerate(development_groups)}
    for fold_number, fold in enumerate(splits):
        training = _fold_group_rows(development_rows, development_groups, fold.train_indices)
        fitted = structural.StructuralModel.fit(
            training,
            candidate=candidate,
            seed=seed + fold_number,
            simulations=STRUCTURAL_SIMULATIONS,
        )
        validation_groups = {development_groups[index] for index in fold.validation_indices}
        for index, row in enumerate(development_rows):
            if _group(row) not in validation_groups or not is_evaluation_row(row):
                continue
            oof[index] = fitted.predict_proba(structural.structural_state_from_row(row))

    final_model = structural.StructuralModel.fit(
        development_rows,
        candidate=candidate,
        seed=seed,
        simulations=STRUCTURAL_SIMULATIONS,
    )
    del group_to_position
    return final_model, oof, candidate


def _disabled_residual(width: int) -> models.ResidualModel:
    return models.ResidualModel(
        intercept=0.0,
        coefficients=(0.0,) * width,
        feature_means=(0.0,) * width,
        feature_scales=(1.0,) * width,
        component_weight=0.0,
    )


def _residual_oof(
    rows: Sequence[FeatureRow],
    group_order: Sequence[str],
    splits: Sequence[WalkForwardSplit],
    feature_names: Sequence[str],
    *,
    seed: int,
) -> dict[int, float]:
    x = _matrix(rows, feature_names)
    prior = [_kalshi_prior(row) for row in rows]
    result: dict[int, float] = {}
    for fold_number, fold in enumerate(splits):
        train_groups = list(fold.train_indices)
        validation_groups = {group_order[index] for index in fold.validation_indices}
        if len(train_groups) < 3:
            continue
        inner_validation_count = max(1, len(train_groups) // 4)
        inner_train = train_groups[:-inner_validation_count]
        inner_validation = train_groups[-inner_validation_count:]
        train_positions = [index for index, row in enumerate(rows) if _group(row) in {group_order[i] for i in inner_train}]
        gate_positions = [index for index, row in enumerate(rows) if _group(row) in {group_order[i] for i in inner_validation}]
        predict_positions = [index for index, row in enumerate(rows) if _group(row) in validation_groups and is_evaluation_row(row)]
        if not train_positions or not gate_positions or not predict_positions:
            continue
        try:
            fitted = models.fit_residual_model(
                [x[index] for index in train_positions],
                [rows[index].label_yes for index in train_positions],
                [prior[index] for index in train_positions],
                [x[index] for index in gate_positions],
                [rows[index].label_yes for index in gate_positions],
                [prior[index] for index in gate_positions],
                seed=seed + fold_number,
            )
        except Exception:
            fitted = _disabled_residual(len(feature_names))
        values = fitted.predict([x[index] for index in predict_positions], [prior[index] for index in predict_positions])
        for index, value in zip(predict_positions, values):
            result[index] = value
    return result


def _final_residual(
    rows: Sequence[FeatureRow],
    group_order: Sequence[str],
    splits: Sequence[WalkForwardSplit],
    feature_names: Sequence[str],
    *,
    seed: int,
) -> models.ResidualModel:
    internal_holdout = set(splits[0].lockbox_indices)
    gate_groups = {group_order[index] for index in internal_holdout}
    train_positions = [index for index, row in enumerate(rows) if _group(row) not in gate_groups]
    gate_positions = [index for index, row in enumerate(rows) if _group(row) in gate_groups]
    x = _matrix(rows, feature_names)
    prior = [_kalshi_prior(row) for row in rows]
    try:
        return models.fit_residual_model(
            [x[index] for index in train_positions],
            [rows[index].label_yes for index in train_positions],
            [prior[index] for index in train_positions],
            [x[index] for index in gate_positions],
            [rows[index].label_yes for index in gate_positions],
            [prior[index] for index in gate_positions],
            seed=seed,
        )
    except Exception:
        return _disabled_residual(len(feature_names))


def _stacker_and_component_ablations(
    rows: Sequence[FeatureRow],
    historical_oof: Sequence[float | None],
    structural_oof: Mapping[int, float],
    residual_oof: Mapping[int, float],
) -> tuple[models.Stacker, artifact.AblationEvidence, artifact.AblationEvidence]:
    positions = [
        index
        for index, prediction in enumerate(historical_oof)
        if prediction is not None and index in structural_oof and index in residual_oof and is_evaluation_row(rows[index])
    ]
    positions.sort(key=lambda index: rows[index].checkpoint_ts_ns)
    if len(positions) < 6:
        raise TrainingError("insufficient aligned OOF rows for ensemble selection")
    holdout_count = max(2, len(positions) // 3)
    fit_positions = positions[:-holdout_count]
    holdout_positions = positions[-holdout_count:]

    def components(selected: Sequence[int]) -> dict[str, list[float]]:
        return {
            "kalshi_prior": [_kalshi_prior(rows[index]) for index in selected],
            "historical_ml": [float(historical_oof[index]) for index in selected],
            "structural": [float(structural_oof[index]) for index in selected],
            "residual_corrected": [float(residual_oof[index]) for index in selected],
        }

    fit_components = components(fit_positions)
    holdout_components = components(holdout_positions)
    fit_labels = [rows[index].label_yes for index in fit_positions]
    holdout_labels = [rows[index].label_yes for index in holdout_positions]

    full = models.fit_stacker(fit_components, fit_labels)
    full_metrics = probability_metrics(holdout_labels, full.predict({name: values for name, values in holdout_components.items() if full.weights.get(name, 0.0) > 0.0}))

    without_struct_fit = {name: values for name, values in fit_components.items() if name != "structural"}
    without_struct_holdout = {name: values for name, values in holdout_components.items() if name != "structural"}
    no_struct = models.fit_stacker(without_struct_fit, fit_labels)
    no_struct_metrics = probability_metrics(
        holdout_labels,
        no_struct.predict({name: values for name, values in without_struct_holdout.items() if no_struct.weights.get(name, 0.0) > 0.0}),
    )
    structural_evidence = _ablation_from_metrics("structural_component", full_metrics, no_struct_metrics)

    without_resid_fit = {name: values for name, values in fit_components.items() if name != "residual_corrected"}
    without_resid_holdout = {name: values for name, values in holdout_components.items() if name != "residual_corrected"}
    no_resid = models.fit_stacker(without_resid_fit, fit_labels)
    no_resid_metrics = probability_metrics(
        holdout_labels,
        no_resid.predict({name: values for name, values in without_resid_holdout.items() if no_resid.weights.get(name, 0.0) > 0.0}),
    )
    residual_evidence = _ablation_from_metrics("residual_component", full_metrics, no_resid_metrics)

    active_names = {"kalshi_prior", "historical_ml"}
    if structural_evidence.retained:
        active_names.add("structural")
    if residual_evidence.retained:
        active_names.add("residual_corrected")
    all_components = components(positions)
    final_components = {name: values for name, values in all_components.items() if name in active_names}
    final_labels = [rows[index].label_yes for index in positions]
    return models.fit_stacker(final_components, final_labels), structural_evidence, residual_evidence


def _ablation_from_metrics(name: str, full: ProbabilityMetrics, ablated: ProbabilityMetrics) -> artifact.AblationEvidence:
    full_snapshot = artifact.metric_snapshot(full)
    ablated_snapshot = artifact.metric_snapshot(ablated)
    nonworse = full.log_loss <= ablated.log_loss + 1e-12 and full.brier <= ablated.brier + 1e-12
    improved = full.log_loss < ablated.log_loss - 1e-6 or full.brier < ablated.brier - 1e-6
    retained = nonworse and improved
    return artifact.AblationEvidence(
        name=name,
        full_metrics=full_snapshot,
        ablated_metrics=ablated_snapshot,
        retained=retained,
        reason=(
            "later-period probability metrics improved with this family/component"
            if retained
            else "excluded because the family/component did not add later-period probability value"
        ),
    )


def _family_ablation(
    name: str,
    full: models.CandidateResults,
    ablated: models.CandidateResults,
    full_rows: Sequence[FeatureRow],
    ablated_rows: Sequence[FeatureRow],
) -> artifact.AblationEvidence:
    return _ablation_from_metrics(
        name,
        _later_metrics(full, full_rows),
        _later_metrics(ablated, ablated_rows),
    )


def _library_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("pydantic", "numpy", "scikit-learn", "scipy", "xgboost-cpu", "pyarrow", "duckdb"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _training_config_hash(settings: BootstrapSettings) -> str:
    payload = {
        "model_version": MODEL_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "settings": settings.model_dump(mode="json"),
        "structural_simulations": STRUCTURAL_SIMULATIONS,
        "evaluation_checkpoint_seconds": EVALUATION_CHECKPOINT_SECONDS,
        "feature_ablations": ("external_btc_features", "kalshi_flow_features"),
        "component_ablations": ("structural_component", "residual_component", "xgboost_vs_lower_variance"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_bundle_features(outcome_features: Sequence[str], dataset_features: Sequence[str]) -> tuple[str, ...]:
    available = set(dataset_features)
    needed = set(outcome_features) | set(STRUCTURAL_FEATURES) | set(PRIOR_FEATURES)
    missing = sorted(needed - available)
    if missing:
        raise TrainingError(f"dataset lacks required live feature {missing[0]}")
    return tuple(sorted(needed))


def build_experiment_bundle(root: Path, settings: BootstrapSettings, *, git_sha: str) -> artifact.ModelBundle:
    context = _verified_context(root)
    pre_lockbox_rows = _read_rows_before_lockbox(context)
    development_rows = _rows_for_groups(pre_lockbox_rows, context.plan.development_groups)
    calibration_rows = _rows_for_groups(pre_lockbox_rows, context.plan.calibration_groups)
    if not development_rows or not calibration_rows:
        raise TrainingError("development/calibration partition is empty")
    if any(_group(row) in set(context.plan.lockbox_groups) for row in pre_lockbox_rows):
        raise TrainingError("lockbox group entered training materialization")

    leakage = audit_dataset_rows((*development_rows, *calibration_rows))
    if not leakage.passed:
        raise TrainingError("development/calibration leakage audit failed")
    splits = _development_splits(development_rows, context.plan.development_groups)

    full_result = models.train_candidate_models(development_rows, splits, seed=settings.random_seed)
    no_btc_rows = _filtered_rows(development_rows, remove_btc=True)
    no_btc_result = models.train_candidate_models(no_btc_rows, splits, seed=settings.random_seed)
    btc_ablation = _family_ablation("external_btc_features", full_result, no_btc_result, development_rows, no_btc_rows)

    no_flow_rows = _filtered_rows(development_rows, remove_kalshi_flow=True)
    no_flow_result = models.train_candidate_models(no_flow_rows, splits, seed=settings.random_seed)
    flow_ablation = _family_ablation("kalshi_flow_features", full_result, no_flow_result, development_rows, no_flow_rows)

    remove_btc = not btc_ablation.retained
    remove_flow = not flow_ablation.retained
    active_rows = _filtered_rows(development_rows, remove_btc=remove_btc, remove_kalshi_flow=remove_flow)
    active_result = models.train_candidate_models(active_rows, splits, seed=settings.random_seed)

    lower_variance = min(
        ("logistic", "hist_gradient_boosting"),
        key=lambda name: (_later_metrics(active_result, active_rows, name).log_loss, _later_metrics(active_result, active_rows, name).brier),
    )
    xgb_ablation = _ablation_from_metrics(
        "xgboost_vs_lower_variance",
        _later_metrics(active_result, active_rows, "xgboost"),
        _later_metrics(active_result, active_rows, lower_variance),
    )
    outcome_name = "xgboost" if xgb_ablation.retained else lower_variance
    outcome_model = active_result.final_models[outcome_name]
    logistic_model = active_result.final_models["logistic"]
    outcome_features = active_result.feature_names

    structural_model, structural_oof, structural_name = _structural_training(
        development_rows,
        context.plan.development_groups,
        splits,
        seed=settings.random_seed,
    )
    residual_oof = _residual_oof(
        active_rows,
        context.plan.development_groups,
        splits,
        outcome_features,
        seed=settings.random_seed,
    )
    final_residual = _final_residual(
        active_rows,
        context.plan.development_groups,
        splits,
        outcome_features,
        seed=settings.random_seed,
    )
    historical_oof = active_result.oof_predictions[outcome_name]
    stacker, structural_ablation, residual_ablation = _stacker_and_component_ablations(
        active_rows,
        historical_oof,
        structural_oof,
        residual_oof,
    )

    evaluation_calibration_rows = tuple(row for row in calibration_rows if is_evaluation_row(row))
    if len(evaluation_calibration_rows) < 6 or len({row.label_yes for row in evaluation_calibration_rows}) < 2:
        raise TrainingError("calibration partition lacks enough predeclared evaluable rows/classes")
    required_features = _required_bundle_features(outcome_features, context.feature_names)
    _require_feature_schema(evaluation_calibration_rows, required_features)
    calibration_x = _matrix(evaluation_calibration_rows, outcome_features)
    prior_p = [_kalshi_prior(row) for row in evaluation_calibration_rows]
    historical_p = _predict_classifier(outcome_model, calibration_x)
    logistic_p = _predict_classifier(logistic_model, calibration_x)
    structural_p = [structural_model.predict_proba(structural.structural_state_from_row(row)) for row in evaluation_calibration_rows]
    residual_p = final_residual.predict(calibration_x, prior_p)
    components = {
        "kalshi_prior": prior_p,
        "historical_ml": historical_p,
        "structural": structural_p,
        "residual_corrected": residual_p,
    }
    active_components = {name: values for name, values in components.items() if stacker.weights.get(name, 0.0) > 0.0}
    uncalibrated = stacker.predict(active_components)
    calibrator = calibration.fit_calibrator(uncalibrated, [row.label_yes for row in evaluation_calibration_rows])
    calibrated = calibrator.predict(uncalibrated)
    labels = [row.label_yes for row in evaluation_calibration_rows]

    pipeline = FittedBootstrapPipeline(
        outcome_feature_names=outcome_features,
        required_feature_names=required_features,
        outcome_model_name=outcome_name,
        outcome_model=outcome_model,
        logistic_model=logistic_model,
        structural_model=structural_model,
        residual_model=final_residual,
        stacker=stacker,
        calibrator=calibrator,
    )
    payload = pickle.dumps(pipeline, protocol=5)
    payload_hash = hashlib.sha256(payload).hexdigest()

    metrics = {
        "candidate": artifact.metric_snapshot(probability_metrics(labels, calibrated)),
        "kalshi_prior": artifact.metric_snapshot(probability_metrics(labels, prior_p)),
        "structural": artifact.metric_snapshot(probability_metrics(labels, structural_p)),
        "logistic": artifact.metric_snapshot(probability_metrics(labels, logistic_p)),
        "naive_50": artifact.metric_snapshot(probability_metrics(labels, [0.5] * len(labels))),
    }
    ablations = (btc_ablation, flow_ablation, structural_ablation, residual_ablation, xgb_ablation)
    excluded_families = tuple(
        name
        for name, evidence in (
            ("external_btc_features", btc_ablation),
            ("kalshi_flow_features", flow_ablation),
        )
        if not evidence.retained
    )
    excluded_components = tuple(
        name
        for name, evidence in (
            ("structural_component", structural_ablation),
            ("residual_component", residual_ablation),
            ("xgboost", xgb_ablation),
        )
        if not evidence.retained
    )
    component_identities = (
        artifact.ComponentIdentity(name="kalshi_prior", kind="benchmark", identity="kalshi_mid_with_trade_fallback", weight=stacker.weights.get("kalshi_prior", 0.0)),
        artifact.ComponentIdentity(name="structural", kind="structural", identity=structural_name, weight=stacker.weights.get("structural", 0.0)),
        artifact.ComponentIdentity(name="historical_ml", kind="model", identity=outcome_name, weight=stacker.weights.get("historical_ml", 0.0)),
        artifact.ComponentIdentity(name="residual_corrected", kind="residual", identity="fixed_unit_kalshi_logit_offset", weight=stacker.weights.get("residual_corrected", 0.0)),
        artifact.ComponentIdentity(name="stacker", kind="stacker", identity="nonnegative_simplex", weight=1.0),
        artifact.ComponentIdentity(name="calibrator", kind="calibrator", identity=calibrator.method, weight=1.0),
    )
    finding_codes = tuple(sorted({finding.code for finding in leakage.findings}))
    return artifact.ModelBundle(
        stage="experiment",
        model_version=MODEL_VERSION,
        git_sha=git_sha,
        random_seed=settings.random_seed,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=required_features,
        boundaries=context.plan.boundaries,
        input_hashes=context.input_hashes,
        training_config_hash=_training_config_hash(settings),
        library_versions=_library_versions(),
        components=component_identities,
        calibration_method=calibrator.method,
        leakage_audit=artifact.LeakageEvidence(
            passed=leakage.passed,
            finding_count=leakage.finding_count,
            finding_codes=finding_codes,
        ),
        metrics=metrics,
        ablations=ablations,
        excluded_feature_families=excluded_families,
        excluded_components=excluded_components,
        serialized_pipeline_b64=base64.b64encode(payload).decode("ascii"),
        serialized_pipeline_sha256=payload_hash,
    )


def partition_row_counts(root: Path, boundaries: artifact.SplitBoundaries) -> tuple[int, int, int]:
    """Return counts without exposing lockbox labels/features to the training path."""
    try:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        table = pq.read_table(_dataset_paths(root)[0], columns=["checkpoint_ts_ns"])
        column = table["checkpoint_ts_ns"]
        train = pc.sum(pc.less_equal(column, boundaries.train_end_ts_ns)).as_py() or 0
        calibration_count = pc.sum(
            pc.and_(
                pc.greater_equal(column, boundaries.calibration_start_ts_ns),
                pc.less_equal(column, boundaries.calibration_end_ts_ns),
            )
        ).as_py() or 0
        lockbox = pc.sum(
            pc.and_(
                pc.greater_equal(column, boundaries.lockbox_start_ts_ns),
                pc.less_equal(column, boundaries.lockbox_end_ts_ns),
            )
        ).as_py() or 0
        return int(train), int(calibration_count), int(lockbox)
    except Exception:
        return 0, 0, 0
