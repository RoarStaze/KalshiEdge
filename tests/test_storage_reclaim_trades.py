"""Rapid disk reclaim must preserve the verified fitted-data snapshot and fail closed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kalshi_edge.storage_maintenance import (
    prune_historical_trades, is_historical_trade_history_pruned,
    verify_retained_training_dataset,
)
from kalshi_edge.bootstrap import dataset


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "KalshiEdge"
    bootstrap = root / "data" / "bootstrap"
    derived = bootstrap / "derived"
    derived.mkdir(parents=True)
    content = b"verified snapshot placeholder"
    digest = hashlib.sha256(content).hexdigest()
    (derived / "features.parquet").write_bytes(content)
    manifest = bootstrap / "manifests" / "dataset" / "features.parquet.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "sha256": digest, "byte_count": len(content),
        "metadata": {"schema_version": 5, "feature_names": ["btc_close"]},
    }), encoding="utf-8")
    (derived / "features.provenance.json").write_text(
        json.dumps({"dataset_sha256": digest,
                    "inputs": [{"path": "raw/kalshi/trades/old.json", "sha256": "a" * 64}]}),
        encoding="utf-8",
    )
    models = bootstrap / "models"
    models.mkdir()
    (models / "default.json").write_text('{"model": "retained"}', encoding="utf-8")
    trades = bootstrap / "raw" / "kalshi" / "trades"
    trades.mkdir(parents=True)
    (trades / "old.json").write_bytes(b"x" * 4096)
    manifest_trades = bootstrap / "manifests" / "kalshi" / "trades"
    manifest_trades.mkdir(parents=True)
    (manifest_trades / "old.json.manifest.json").write_text('{"sha256": "dummy"}')
    markets = bootstrap / "raw" / "kalshi" / "markets"
    markets.mkdir(parents=True)
    (markets / "old.json").write_text('{"ticker": "OLD"}')
    (bootstrap / "final_forecasts").mkdir()
    (bootstrap / "final_forecasts" / "keep.json").write_text("{}")
    ws = root / "data" / "raw" / "source=kalshi_ws"
    ws.mkdir(parents=True)
    (ws / "record.jsonl").write_text("{}")
    (root / "src" / "kalshi_edge").mkdir(parents=True)
    return root


def test_preview_does_not_delete_or_write_marker(tmp_path):
    root = _root(tmp_path)
    report = prune_historical_trades(root, apply=False)
    assert report["applied"] is False
    assert report["verified_dataset"] is True
    assert report["dataset_sha256"]
    assert (root / "data/bootstrap/raw/kalshi/trades/old.json").exists()
    assert not is_historical_trade_history_pruned(root / "data/bootstrap")


def test_apply_removes_only_trade_raw_and_matching_manifests(tmp_path):
    root = _root(tmp_path)
    result = prune_historical_trades(root, apply=True)
    bootstrap = root / "data/bootstrap"
    assert result["applied"] is True
    assert is_historical_trade_history_pruned(bootstrap)
    assert not (bootstrap / "raw/kalshi/trades").exists()
    assert not (bootstrap / "manifests/kalshi/trades").exists()
    assert (bootstrap / "derived/features.parquet").exists()
    assert (bootstrap / "derived/features.provenance.json").exists()
    assert (bootstrap / "manifests/dataset/features.parquet.manifest.json").exists()
    assert (bootstrap / "raw/kalshi/markets/old.json").exists()
    assert (bootstrap / "models/default.json").exists()
    assert (bootstrap / "final_forecasts/keep.json").exists()
    assert (root / "data/raw/source=kalshi_ws/record.jsonl").exists()
    verify_retained_training_dataset(bootstrap)


def test_missing_verified_dataset_blocks_deletion(tmp_path):
    root = _root(tmp_path)
    (root / "data/bootstrap/derived/features.parquet").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="verification"):
        prune_historical_trades(root, apply=True)
    assert (root / "data/bootstrap/raw/kalshi/trades/old.json").exists()
    assert not is_historical_trade_history_pruned(root / "data/bootstrap")


def test_missing_promoted_model_blocks_deletion(tmp_path):
    root = _root(tmp_path)
    (root / "data/bootstrap/models/default.json").unlink()
    with pytest.raises(ValueError, match="promoted"):
        prune_historical_trades(root, apply=True)
    assert (root / "data/bootstrap/raw/kalshi/trades/old.json").exists()


def test_missing_manifest_directory_blocks_first_deletion(tmp_path):
    root = _root(tmp_path)
    (root / "data/bootstrap/manifests/kalshi/trades/old.json.manifest.json").unlink()
    (root / "data/bootstrap/manifests/kalshi/trades").rmdir()
    with pytest.raises(ValueError, match="manifest"):
        prune_historical_trades(root, apply=True)
    assert (root / "data/bootstrap/raw/kalshi/trades/old.json").exists()


def test_recovery_after_partial_removal_is_idempotent(tmp_path):
    root = _root(tmp_path)
    prune_historical_trades(root, apply=True)
    result = prune_historical_trades(root, apply=True)
    assert result["applied"] is True
    assert result["already_pruned"] is True


def test_dataset_rebuild_blocks_pruned_raw_history_before_writing(tmp_path):
    root = _root(tmp_path)
    prune_historical_trades(root, apply=True)
    source = root / "data/bootstrap/derived/features.parquet"
    old = source.read_bytes()
    with pytest.raises(dataset.DatasetBuildError, match="pruned"):
        dataset.build_dataset(root / "data/bootstrap")
    assert source.read_bytes() == old


def test_junction_or_symlinked_trade_directory_is_rejected(tmp_path):
    root = _root(tmp_path)
    trading = root / "data/bootstrap/raw/kalshi/trades"
    (trading / "old.json").unlink()
    trading.rmdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "dont-delete.txt").write_text("KEEP")
    try:
        trading.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not permitted")
    with pytest.raises(ValueError, match="link|junction|external"):
        prune_historical_trades(root, apply=True)
    assert (external / "dont-delete.txt").exists()
