from __future__ import annotations

"""One-time volatility challenger: historical fit, fresh 240s lockbox, conditional promotion.

Does not fit a new BTC direction model. Reuses the currently promoted V5
pipeline and selects a small causal volatility probability correction using
earlier calibration markets. A strictly newer untouched market interval is
reserved BEFORE any labels for evaluation are materialized.
"""

import base64
import hashlib
import json
import pickle
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import artifact, training, volatility
from .config import BootstrapSettings
from .leakage import audit_dataset_rows
from .metrics import probability_metrics

MIN_FRESH_LOCKBOX_MARKETS = 120
MAX_CALIBRATION_MARKETS = 600
UPGRADE_MARKER_DIR = "volatility_challengers"


class VolatilityUpgradeError(RuntimeError):
    """The upgrade cannot proceed without valid fresh causal evidence."""


def _selection_rows(context: training.DatasetContext):
    """Load ONLY past 240-second calibration markets; no lockbox row is read."""

    import pyarrow.parquet as pq

    calibration_tickers = context.plan.calibration_groups[-MAX_CALIBRATION_MARKETS:]
    schema = pq.read_schema(context.dataset_path)
    columns = [
        "market_ticker", "market_date", "split_group_id",
        "checkpoint_ts_ns", "label_yes",
        *context.feature_names,
        *(name for name in schema.names if name.startswith("source_ts__")),
    ]
    table = pq.read_table(
        context.dataset_path,
        columns=columns,
        filters=[
            ("market_ticker", "in", list(calibration_tickers)),
            ("seconds_remaining", ">=", volatility.FINAL_LOCK_SECONDS - volatility.CHECKPOINT_TOLERANCE_SECONDS),
            ("seconds_remaining", "<=", volatility.FINAL_LOCK_SECONDS + volatility.CHECKPOINT_TOLERANCE_SECONDS),
            ("checkpoint_ts_ns", "<", context.plan.boundaries.lockbox_start_ts_ns),
        ],
    )
    rows = training._table_to_rows(table, context.feature_names)
    allowed = set(calibration_tickers)
    selected: dict[str, Any] = {}
    for row in rows:
        if row.market_ticker not in allowed or not volatility.is_final_checkpoint(row):
            continue
        if not (context.plan.boundaries.calibration_start_ts_ns <= row.checkpoint_ts_ns <= context.plan.boundaries.calibration_end_ts_ns):
            raise VolatilityUpgradeError("calibration row escapes the predeclared historical calibration period")
        prior = selected.get(row.market_ticker)
        distance = abs(row.features["seconds_remaining"] - volatility.FINAL_LOCK_SECONDS)
        if prior is None or distance < abs(prior.features["seconds_remaining"] - volatility.FINAL_LOCK_SECONDS):
            selected[row.market_ticker] = row
    result = tuple(sorted(selected.values(), key=lambda row: (row.checkpoint_ts_ns, row.market_ticker)))
    if len(result) < volatility.MIN_CALIBRATION_MARKETS:
        raise VolatilityUpgradeError(
            f"need {volatility.MIN_CALIBRATION_MARKETS} historical 240s calibration markets; found {len(result)}"
        )
    evidence = audit_dataset_rows(result)
    if not evidence.passed:
        raise VolatilityUpgradeError("causal volatility calibration data failed leakage audit")
    return result


def preflight(root: Path) -> dict[str, Any]:
    """Fast metadata eligibility check, without decoding model or reading labels."""

    root = root.resolve()
    previous = training._previous_lockbox_end_ts_ns(root)
    base = {
        "ready": False,
        "previous_lockbox_end_ts_ns": previous,
        "fresh_market_count": 0,
        "required_fresh_markets": MIN_FRESH_LOCKBOX_MARKETS,
        "required_calibration_markets": volatility.MIN_CALIBRATION_MARKETS,
        "reason": "",
    }
    if previous is None:
        base["reason"] = "A previously evaluated and promoted V5 baseline model is required."
        return base
    try:
        # Verify the immutable dataset/manifests and chronology before any labels.
        context = training._verified_context(root)
        groups = training._metadata_groups(context.dataset_path)
        fresh = [g for g in groups if g[1] > previous]
        base["fresh_market_count"] = len(fresh)
        if len(fresh) < MIN_FRESH_LOCKBOX_MARKETS:
            base["reason"] = (
                f"Only {len(fresh)}/{MIN_FRESH_LOCKBOX_MARKETS} new causal market groups "
                "since the last evaluated lockbox. Backfill recent BTC/Kalshi history "
                "and rebuild the dataset; do not reuse a previous lockbox."
            )
            return base
        if len(context.plan.calibration_groups) < volatility.MIN_CALIBRATION_MARKETS:
            base["reason"] = "Insufficient historic calibration markets."
            return base
        from .live import load_default_bundle
        incumbent = load_default_bundle(root)
        base["incumbent_bundle_sha256"] = incumbent.bundle_sha256
        base["ready"] = True
        base["reason"] = "Ready to select volatility weight from historical calibration, then evaluate one untouched lockbox."
    except Exception as exc:
        base["reason"] = f"{type(exc).__name__}: {exc}"
    return base


def _manifest(root: Path, experiment: artifact.ModelBundle) -> dict[str, Any]:
    markers = [
        (relative, sha) for relative, sha in experiment.input_hashes.items()
        if relative.startswith(f"{UPGRADE_MARKER_DIR}/")
    ]
    if len(markers) != 1:
        raise VolatilityUpgradeError("volatility challenger requires exactly one immutable source marker")
    relative, digest = markers[0]
    marker_root = (root / UPGRADE_MARKER_DIR).resolve()
    path = (root / relative).resolve()
    if marker_root not in path.parents or path.suffix != ".json":
        raise VolatilityUpgradeError("volatility upgrade source marker escaped dedicated directory")
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise VolatilityUpgradeError("volatility source marker SHA-256 mismatch")
        payload = json.loads(content)
    except (ValueError, OSError, TypeError) as exc:
        raise VolatilityUpgradeError("volatility source marker unreadable") from exc
    if (
        payload.get("policy") != volatility.VOLATILITY_POLICY
        or not isinstance(payload.get("incumbent_bundle_sha256"), str)
        or len(payload["incumbent_bundle_sha256"]) != 64
        or payload.get("weight") not in volatility.WEIGHTS[1:]
        or path.name != hashlib.sha256(content).hexdigest() + ".json"
    ):
        raise VolatilityUpgradeError("volatility source marker fields invalid")
    return payload


def verify_challenger(root: Path, experiment: artifact.ModelBundle) -> None:
    """Fail before loading ANY new lockbox label if incumbent/dataset changed."""

    if not any(k.startswith(f"{UPGRADE_MARKER_DIR}/") for k in experiment.input_hashes):
        raise VolatilityUpgradeError("not a volatility challenger")
    incumbent_hash = _manifest(root, experiment)["incumbent_bundle_sha256"]
    from .live import load_default_bundle
    incumbent = load_default_bundle(root)
    if incumbent.bundle_sha256 != incumbent_hash:
        raise VolatilityUpgradeError("default model changed since volatility challenger training")
    context = training._verified_context(root)
    if experiment.boundaries != context.plan.boundaries:
        raise VolatilityUpgradeError("fresh lockbox boundaries changed since candidate was trained")
    if experiment.input_hashes.get("derived/features.parquet") != context.input_hashes["derived/features.parquet"]:
        raise VolatilityUpgradeError("causal feature dataset changed since candidate was trained")
    try:
        pipeline = pickle.loads(artifact.serialized_pipeline_bytes(experiment))
    except Exception as exc:
        raise VolatilityUpgradeError("candidate pipeline could not be decoded") from exc
    if not isinstance(pipeline, training.FittedBootstrapPipeline):
        raise VolatilityUpgradeError("candidate model is not a fitted KalshiEdge pipeline")
    overlay = getattr(pipeline, "volatility_overlay", None)
    if not isinstance(overlay, volatility.VolatilityOverlay) or overlay.weight != _manifest(root, experiment)["weight"]:
        raise VolatilityUpgradeError("serialized challenger does not match its source marker")


def build_candidate(root: Path, *, git_sha: str) -> tuple[artifact.ModelBundle | None, dict[str, Any]]:
    """Use historic 240s data to select a fixed volatility blend—NO lockbox access."""

    root = root.resolve()
    eligibility = preflight(root)
    if not eligibility["ready"]:
        raise VolatilityUpgradeError(eligibility["reason"])

    from .live import load_default_model
    incumbent, incumbent_pipeline = load_default_model(root)
    context = training._verified_context(root)
    selection_rows = _selection_rows(context)
    prior_predictions = incumbent_pipeline.base_candidate(selection_rows)
    selection = volatility.select_volatility_overlay(selection_rows, prior_predictions)
    summary = {
        "candidate_type": volatility.VOLATILITY_POLICY,
        "incumbent_bundle_sha256": incumbent.bundle_sha256,
        "selected_weight": selection.selected_weight,
        "fit_markets": selection.fit_markets,
        "validation_markets": selection.validation_markets,
        "validation_baseline": selection.validation_baseline.model_dump(mode="json", exclude={"reliability_bins"}),
        "validation_challenger": selection.validation_challenger.model_dump(mode="json", exclude={"reliability_bins"}),
        "accepted_for_lockbox": selection.accepted,
        "decision": selection.reason,
        "evaluation_lockbox": {
            "start_ts_ns": context.plan.boundaries.lockbox_start_ts_ns,
            "end_ts_ns": context.plan.boundaries.lockbox_end_ts_ns,
        },
    }
    if not selection.accepted or selection.overlay is None:
        return None, summary

    features = tuple(sorted(set(incumbent.feature_names) | {
        "btc_close", "strike", "seconds_remaining",
        "btc_realized_vol_60s", "btc_realized_vol_300s",
    }))
    if not set(features).issubset(context.feature_names):
        raise VolatilityUpgradeError("volatility challenger features unavailable in causal dataset")

    # The promoted bundle and fitted pipeline must have exactly matching
    # feature manifests; live inference refuses mismatched schemas.
    enhanced = replace(
        incumbent_pipeline, required_feature_names=features,
        volatility_overlay=selection.overlay,
    )
    serialized = pickle.dumps(enhanced, protocol=pickle.HIGHEST_PROTOCOL)
    weight = selection.overlay.weight

    marker = {
        "policy": volatility.VOLATILITY_POLICY,
        "incumbent_bundle_sha256": incumbent.bundle_sha256,
        "weight": weight,
        "fit_market_count": selection.fit_markets,
        "validation_market_count": selection.validation_markets,
        "calibration_boundary_ns": context.plan.boundaries.calibration_end_ts_ns,
        "reserved_lockbox_start_ns": context.plan.boundaries.lockbox_start_ts_ns,
        "dataset_sha256": context.input_hashes["derived/features.parquet"],
    }
    marker_bytes = (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    marker_digest = hashlib.sha256(marker_bytes).hexdigest()
    marker_path = root / UPGRADE_MARKER_DIR / f"{marker_digest}.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker_path.open("xb") as out:
            out.write(marker_bytes)
    except FileExistsError:
        if marker_path.read_bytes() != marker_bytes:
            raise VolatilityUpgradeError("existing source marker content mismatch")

    config_bytes = json.dumps({
        "git_sha": git_sha,
        "policy": volatility.VOLATILITY_POLICY,
        "weight": weight,
        "incumbent_bundle_sha256": incumbent.bundle_sha256,
        "calibration_market_count": len(selection_rows),
        "dataset_sha256": context.input_hashes["derived/features.parquet"],
    }, sort_keys=True, separators=(",", ":")).encode()

    challenger = incumbent.model_copy(update={
        "stage": "experiment",
        "git_sha": git_sha,
        "feature_names": features,
        "boundaries": context.plan.boundaries,
        "input_hashes": {
            **context.input_hashes,
            marker_path.relative_to(root).as_posix(): marker_digest,
        },
        "training_config_hash": hashlib.sha256(config_bytes).hexdigest(),
        "components": (
            *tuple(component for component in incumbent.components if component.name != "volatility_overlay"),
            artifact.ComponentIdentity(
                name="volatility_overlay",
                kind="calibrator",
                identity=volatility.VOLATILITY_POLICY,
                weight=weight,
            ),
        ),
        "metrics": {
            "candidate": artifact.metric_snapshot(selection.validation_challenger),
            "incumbent_current": artifact.metric_snapshot(selection.validation_baseline),
        },
        "serialized_pipeline_b64": base64.b64encode(serialized).decode("ascii"),
        "serialized_pipeline_sha256": hashlib.sha256(serialized).hexdigest(),
        "source_experiment_sha256": None,
        "promotion_rule": None,
        "promotion_reason_codes": (),
        "bundle_sha256": None,
    })
    return challenger, summary


def run_upgrade(root: Path, *, git_sha: str) -> dict[str, Any]:
    """Fit a volatility challenger and automatically promote *only* on strict pass."""

    from ..launcher import SingleStarter
    from .evaluate import run_lockbox_evaluation

    root = root.resolve()
    with SingleStarter(root / "training_runs" / "worker.lock"):
        challenger, summary = build_candidate(root, git_sha=git_sha)
        if challenger is None:
            return {
                **summary,
                "promoted": False,
                "lockbox_evaluated": False,
                "reason": "No pre-lockbox evidence of improvement. Default model unchanged.",
            }
        experiment_path = artifact.save_model_bundle(challenger, root / "models" / "experiments")
        sealed = artifact.load_model_bundle(experiment_path)
        verify_challenger(root, sealed)
        # Evaluation is the one and only path allowed to promote.
        # A rejected candidate consumes its lockbox; no retry on that interval.
        outcome = run_lockbox_evaluation(root, experiment_path)
        return {
            **summary,
            "experiment_bundle_sha256": sealed.bundle_sha256,
            "evaluation_report_path": str(outcome.report_path),
            "lockbox_evaluated": True,
            "promoted": outcome.decision.promoted,
            "reason_codes": list(outcome.decision.reason_codes),
            "new_model_path": str(outcome.promoted_path) if outcome.promoted_path else None,
            "default_model_pointer": str(outcome.default_path) if outcome.default_path else None,
            "note": (
                "Model promoted. Restart kalshiedge start to load the new bundle."
                if outcome.decision.promoted else
                "Candidate rejected. Existing promoted model remains unchanged."
            ),
        }
