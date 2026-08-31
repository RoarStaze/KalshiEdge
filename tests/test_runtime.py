from kalshi_edge.config import CollectorSettings
from kalshi_edge.runtime import build_runtime_snapshot


def test_runtime_snapshot_records_reproducibility_metadata_without_credentials(tmp_path) -> None:
    settings = CollectorSettings(
        _env_file=None,
        key_id="sensitive-id",
        private_key_path=tmp_path / "private.pem",
        data_dir=tmp_path / "data",
        env="demo",
    )
    snapshot = build_runtime_snapshot(settings, git_commit="abc123")
    serialized = str(snapshot)
    assert snapshot["git_commit"] == "abc123"
    assert snapshot["config"]["env"] == "demo"
    assert snapshot["config"]["series_ticker"] == "KXBTC15M"
    assert "sensitive-id" not in serialized
    assert "private.pem" not in serialized
