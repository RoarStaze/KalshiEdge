from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kalshi_edge.bootstrap.backfill import backfill_binance
from kalshi_edge.bootstrap.binance_history import BinanceDataError
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.dataset import DatasetBuildError, build_dataset
from kalshi_edge.bootstrap.provenance import RawArtifact, sha256_file, write_manifest, write_raw_artifact


def _http_error(status_code: int, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def _market_payload(ticker: str, day: int) -> dict:
    stamp = f"2026-08-{day:02d}"
    return {
        "market": {
            "ticker": ticker,
            "event_ticker": f"{ticker}-EVENT",
            "market_type": "binary",
            "open_time": f"{stamp}T12:00:00Z",
            "close_time": f"{stamp}T12:15:00Z",
            "settlement_ts": f"{stamp}T12:16:00Z",
            "status": "finalized",
            "result": "yes",
            "settlement_value_dollars": "101.0",
            "strike_type": "greater",
            "floor_strike": 100.0,
            "rules_primary": "Resolves Yes if above target.",
            "rules_secondary": "CF Benchmarks",
            "is_provisional": False,
        }
    }


def _trade_payload(ticker: str, day: int) -> dict:
    return {
        "ticker": ticker,
        "trades": [
            {
                "created_time": f"2026-08-{day:02d}T12:05:00Z",
                "yes_price_dollars": "0.55",
                "count_fp": "10.0",
                "taker_side": "yes",
            }
        ],
    }


def _candle_payload(ticker: str, day: int) -> dict:
    end_period_ts = int(datetime(2026, 8, day, 12, 6, tzinfo=timezone.utc).timestamp())
    return {
        "ticker": ticker,
        "candlesticks": [
            {
                "end_period_ts": end_period_ts,
                "yes_bid": {"close": "0.54"},
                "yes_ask": {"close": "0.56"},
                "price": {"close": "0.55", "high": "0.57", "low": "0.53"},
                "volume": "10.0",
                "open_interest": "5.0",
            }
        ],
    }


def _write_json(root: Path, source: str, logical_name: str, payload: dict) -> None:
    write_raw_artifact(
        root=root,
        source=source,
        logical_name=logical_name,
        content=(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        metadata={"source_locator": "test"},
    )


def _seed_market(root: Path, ticker: str, day: int) -> None:
    _write_json(root, "kalshi", f"markets/{ticker}.json", _market_payload(ticker, day))
    _write_json(root, "kalshi", f"trades/{ticker}.json", _trade_payload(ticker, day))
    _write_json(root, "kalshi", f"candlesticks/{ticker}.json", _candle_payload(ticker, day))


def _tiny_zip() -> bytes:
    row = "1735689600000000,49999,50001,49998,50000,2.5,1735689600999999,125000,12,1.4,70000,0\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1s.csv", row)
    return buffer.getvalue()


class _ArchiveClient:
    def __init__(self, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        self.urls: list[str] = []

    def download_and_verify(self, url: str, checksum_url: str, root: Path):
        self.urls.append(url)
        if self.status_code is not None:
            raise _http_error(self.status_code, checksum_url)
        filename = Path(urlparse(url).path).name
        return write_raw_artifact(
            root=root,
            source="binance",
            logical_name=f"archive/{filename}",
            content=_tiny_zip(),
            metadata={"source_locator": url, "official_checksum_sha256": "test"},
        )

    def close(self) -> None:
        pass


def _fake_converter(source: Path, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PAR1-test")
    return 1


def _seed_valid_binance_day(root: Path, day: int) -> None:
    date_text = f"2026-08-{day:02d}"
    raw = write_raw_artifact(
        root=root,
        source="binance",
        logical_name=f"archive/BTCUSDT-1s-{date_text}.zip",
        content=b"archive",
        metadata={"source_locator": "official-test"},
    )
    start = int(datetime(2026, 8, day, 11, 45, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    rows = [
        {
            "ts_ns": start + index * 1_000_000_000,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "base_volume": 1.0,
            "quote_volume": 100.0,
            "trade_count": 1,
            "taker_buy_base": 0.6,
            "taker_buy_quote": 60.0,
        }
        for index in range(1801)
    ]
    normalized = root / "normalized/binance/1s" / f"BTCUSDT-1s-{date_text}.parquet"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), normalized)
    artifact = RawArtifact(
        path=normalized.relative_to(root),
        manifest_path=Path(f"manifests/binance_normalized/1s/BTCUSDT-1s-{date_text}.parquet.manifest.json"),
        sha256=sha256_file(normalized),
        source="binance_normalized",
        retrieval_ts_utc="2026-08-31T00:00:00+00:00",
        byte_count=normalized.stat().st_size,
        metadata={
            "source_raw_path": raw.path.as_posix(),
            "source_raw_sha256": raw.sha256,
            "row_count": len(rows),
        },
    )
    write_manifest(root, artifact)


def test_backfill_skips_current_utc_date_and_records_reproducible_publication_cutoff(tmp_path: Path) -> None:
    _seed_market(tmp_path, "KXBTC15M-OLD", 1)
    _seed_market(tmp_path, "KXBTC15M-CURRENT", 2)
    client = _ArchiveClient()

    report = backfill_binance(
        BootstrapSettings(bootstrap_dir=tmp_path),
        client=client,
        parquet_converter=_fake_converter,
        as_of_utc=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
    )

    assert report.archive_available_through_date == "2026-08-01"
    assert report.unavailable_dates == ("2026-08-02",)
    assert report.dates == ("2026-08-01", "2026-08-02")
    assert len(client.urls) == 1
    assert client.urls[0].endswith("BTCUSDT-1s-2026-08-01.zip")
    snapshot = tmp_path / "raw/binance_availability/daily/2026-08-02.json"
    snapshot_manifest = tmp_path / "manifests/binance_availability/daily/2026-08-02.json.manifest.json"
    assert snapshot.exists() and snapshot_manifest.exists()


def test_backfill_propagates_404_for_date_that_should_already_be_published(tmp_path: Path) -> None:
    _seed_market(tmp_path, "KXBTC15M-OLD", 1)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        backfill_binance(
            BootstrapSettings(bootstrap_dir=tmp_path),
            client=_ArchiveClient(status_code=404),
            parquet_converter=_fake_converter,
            as_of_utc=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
    assert exc_info.value.response.status_code == 404


def test_backfill_propagates_non_404_archive_failure(tmp_path: Path) -> None:
    _seed_market(tmp_path, "KXBTC15M-OLD", 1)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        backfill_binance(
            BootstrapSettings(bootstrap_dir=tmp_path),
            client=_ArchiveClient(status_code=500),
            parquet_converter=_fake_converter,
            as_of_utc=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
    assert exc_info.value.response.status_code == 500


@pytest.mark.parametrize("damage", ["missing_manifest", "hash_mismatch"])
def test_backfill_fails_hard_on_invalid_existing_normalized_provenance(tmp_path: Path, damage: str) -> None:
    _seed_market(tmp_path, "KXBTC15M-OLD", 1)
    settings = BootstrapSettings(bootstrap_dir=tmp_path)
    backfill_binance(
        settings,
        client=_ArchiveClient(),
        parquet_converter=_fake_converter,
        as_of_utc=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    normalized = tmp_path / "normalized/binance/1s/BTCUSDT-1s-2026-08-01.parquet"
    manifest = tmp_path / "manifests/binance_normalized/1s/BTCUSDT-1s-2026-08-01.parquet.manifest.json"
    if damage == "missing_manifest":
        manifest.unlink()
    else:
        normalized.write_bytes(normalized.read_bytes() + b"corrupt")

    with pytest.raises(RuntimeError, match="provenance"):
        backfill_binance(
            settings,
            client=_ArchiveClient(),
            parquet_converter=_fake_converter,
            as_of_utc=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )


def test_dataset_excludes_market_requiring_not_yet_published_binance_day(tmp_path: Path) -> None:
    _seed_market(tmp_path, "KXBTC15M-OLD", 1)
    _seed_market(tmp_path, "KXBTC15M-CURRENT", 2)
    _seed_valid_binance_day(tmp_path, 1)
    backfill_binance(
        BootstrapSettings(bootstrap_dir=tmp_path),
        client=_ArchiveClient(),
        parquet_converter=_fake_converter,
        as_of_utc=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
    )

    report = build_dataset(tmp_path, BootstrapSettings(bootstrap_dir=tmp_path))

    assert report.market_count == 1
    assert report.excluded_markets == ("KXBTC15M-CURRENT",)
    assert report.leakage_finding_count == 0


def test_dataset_fails_if_required_binance_day_is_missing_at_or_before_recorded_cutoff(tmp_path: Path) -> None:
    _seed_market(tmp_path, "KXBTC15M-OLD", 1)
    _seed_valid_binance_day(tmp_path, 1)
    _seed_market(tmp_path, "KXBTC15M-MISSING-OLD", 2)
    backfill_binance(
        BootstrapSettings(bootstrap_dir=tmp_path),
        client=_ArchiveClient(),
        parquet_converter=_fake_converter,
        as_of_utc=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    raw = tmp_path / "raw/binance/archive/BTCUSDT-1s-2026-08-02.zip"
    raw_manifest = tmp_path / "manifests/binance/archive/BTCUSDT-1s-2026-08-02.zip.manifest.json"
    normalized = tmp_path / "normalized/binance/1s/BTCUSDT-1s-2026-08-02.parquet"
    normalized_manifest = tmp_path / "manifests/binance_normalized/1s/BTCUSDT-1s-2026-08-02.parquet.manifest.json"
    for path in (raw, raw_manifest, normalized, normalized_manifest):
        if path.exists():
            path.unlink()

    with pytest.raises(DatasetBuildError, match="Binance"):
        build_dataset(tmp_path, BootstrapSettings(bootstrap_dir=tmp_path))
