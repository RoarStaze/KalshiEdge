from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap import artifact


def _bundle() -> artifact.ModelBundle:
    payload = b"deterministic-pipeline-payload"
    return artifact.ModelBundle(
        stage="experiment",
        model_version="bootstrap-hybrid-v1",
        git_sha="a" * 40,
        random_seed=73115,
        feature_schema_version=1,
        feature_names=("btc_distance_bps", "kalshi_mid"),
        boundaries=artifact.SplitBoundaries(
            train_start_ts_ns=100,
            train_end_ts_ns=200,
            calibration_start_ts_ns=201,
            calibration_end_ts_ns=250,
            lockbox_start_ts_ns=300,
            lockbox_end_ts_ns=400,
        ),
        input_hashes={
            "derived/features.parquet": "b" * 64,
            "raw/kalshi/markets/a.json": "c" * 64,
        },
        training_config_hash="d" * 64,
        library_versions={"python": "3.13.15", "numpy": "2.5.2"},
        components=(
            artifact.ComponentIdentity(name="kalshi_prior", kind="benchmark", identity="kalshi_mid", weight=0.4),
            artifact.ComponentIdentity(name="historical_ml", kind="model", identity="logistic", weight=0.6),
        ),
        calibration_method="platt",
        leakage_audit=artifact.LeakageEvidence(passed=True, finding_count=0, finding_codes=()),
        metrics={
            "kalshi_prior": artifact.MetricSnapshot(brier=0.24, log_loss=0.68, accuracy=0.55, ece=0.08, sharpness=0.12),
            "candidate": artifact.MetricSnapshot(brier=0.21, log_loss=0.62, accuracy=0.61, ece=0.06, sharpness=0.16),
        },
        ablations=(
            artifact.AblationEvidence(
                name="external_btc_features",
                full_metrics=artifact.MetricSnapshot(brier=0.21, log_loss=0.62, accuracy=0.61, ece=0.06, sharpness=0.16),
                ablated_metrics=artifact.MetricSnapshot(brier=0.23, log_loss=0.66, accuracy=0.58, ece=0.07, sharpness=0.14),
                retained=True,
                reason="later-period probability metrics improved",
            ),
        ),
        excluded_feature_families=(),
        excluded_components=(),
        serialized_pipeline_b64=base64.b64encode(payload).decode("ascii"),
        serialized_pipeline_sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_model_bundle_is_hash_addressed_and_round_trips(tmp_path: Path) -> None:
    bundle = _bundle()
    target = artifact.save_model_bundle(bundle, tmp_path / "models" / "experiments")

    assert target.parent == tmp_path / "models" / "experiments"
    assert target.name.endswith(".json")
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["bundle_sha256"] == target.stem
    assert len(stored["bundle_sha256"]) == 64

    loaded = artifact.load_model_bundle(target)
    assert loaded.bundle_sha256 == target.stem
    assert loaded.git_sha == "a" * 40
    assert loaded.random_seed == 73115
    assert loaded.feature_names == ("btc_distance_bps", "kalshi_mid")
    assert loaded.boundaries.lockbox_start_ts_ns == 300
    assert loaded.input_hashes["derived/features.parquet"] == "b" * 64
    assert loaded.library_versions["numpy"] == "2.5.2"
    assert loaded.components[1].identity == "logistic"
    assert loaded.calibration_method == "platt"
    assert loaded.leakage_audit.passed is True
    assert loaded.metrics["candidate"].log_loss == pytest.approx(0.62)
    assert loaded.ablations[0].name == "external_btc_features"


def test_model_bundle_rejects_one_byte_mutation(tmp_path: Path) -> None:
    target = artifact.save_model_bundle(_bundle(), tmp_path / "models" / "experiments")
    content = target.read_bytes()
    target.write_bytes(content.replace(b"bootstrap-hybrid-v1", b"bootstrap-hybrid-v2", 1))

    with pytest.raises(artifact.ArtifactIntegrityError, match="bundle SHA-256"):
        artifact.load_model_bundle(target)


def test_serialized_pipeline_payload_hash_is_verified(tmp_path: Path) -> None:
    bundle = _bundle().model_copy(update={"serialized_pipeline_sha256": "0" * 64})

    with pytest.raises(artifact.ArtifactIntegrityError, match="pipeline payload"):
        artifact.save_model_bundle(bundle, tmp_path / "models" / "experiments")
