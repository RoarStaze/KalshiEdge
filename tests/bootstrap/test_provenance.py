from pathlib import Path

import pytest

from kalshi_edge.bootstrap.provenance import verify_artifact, write_raw_artifact


def test_raw_artifact_is_isolated_manifested_and_hash_verified(tmp_path: Path) -> None:
    root = tmp_path / "data" / "bootstrap"
    artifact = write_raw_artifact(
        root=root,
        source="kalshi",
        logical_name="markets/sample.json",
        content=b'{"ticker":"KXBTC15M-X"}',
        metadata={"source_locator": "https://example.invalid/market", "parser_version": "1"},
    )

    assert artifact.path == Path("raw/kalshi/markets/sample.json")
    assert artifact.manifest_path == Path("manifests/kalshi/markets/sample.json.manifest.json")
    assert (root / artifact.path).read_bytes() == b'{"ticker":"KXBTC15M-X"}'
    assert verify_artifact(root / artifact.path, root / artifact.manifest_path)

    (root / artifact.path).write_bytes(b"mutated")
    assert not verify_artifact(root / artifact.path, root / artifact.manifest_path)


def test_raw_artifact_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "data" / "bootstrap"
    with pytest.raises(ValueError, match="escape"):
        write_raw_artifact(
            root=root,
            source="kalshi",
            logical_name="../../../secret.txt",
            content=b"x",
            metadata={},
        )


def test_bootstrap_writer_rejects_phase1_raw_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Phase 1"):
        write_raw_artifact(
            root=tmp_path / "data" / "raw",
            source="kalshi",
            logical_name="x.json",
            content=b"{}",
            metadata={},
        )
