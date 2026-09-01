from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest

from kalshi_edge.bootstrap.binance_history import (
    BinanceArchiveClient,
    BinanceDataError,
    archive_urls,
    inspect_spot_1s,
    normalize_epoch_to_ns,
    parse_spot_1s,
)
from kalshi_edge.bootstrap.provenance import verify_artifact


def _zip_bytes(rows: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1s.csv", "\n".join(rows) + "\n")
    return buffer.getvalue()


def _row(ts: int, close: str = "50000.0") -> str:
    return ",".join(
        [
            str(ts),
            "49999.0",
            "50001.0",
            "49998.0",
            close,
            "2.5",
            str(ts + 999_999),
            "125000.0",
            "12",
            "1.4",
            "70000.0",
            "0",
        ]
    )


def test_archive_urls_use_official_daily_spot_layout() -> None:
    urls = archive_urls("btcusdt", [date(2026, 8, 30), date(2026, 8, 31)])
    assert urls == [
        "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-2026-08-30.zip",
        "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-2026-08-31.zip",
    ]


def test_normalize_epoch_to_ns_handles_ms_us_and_ns() -> None:
    assert normalize_epoch_to_ns(1_735_689_600_000) == 1_735_689_600_000_000_000
    assert normalize_epoch_to_ns(1_735_689_600_000_000) == 1_735_689_600_000_000_000
    assert normalize_epoch_to_ns(1_735_689_600_000_000_000) == 1_735_689_600_000_000_000
    with pytest.raises(BinanceDataError):
        normalize_epoch_to_ns(123)


def test_parse_spot_1s_normalizes_microseconds_and_reports_gaps(tmp_path: Path) -> None:
    base = 1_735_689_600_000_000
    path = tmp_path / "bars.zip"
    path.write_bytes(_zip_bytes([_row(base), _row(base + 1_000_000), _row(base + 3_000_000)]))

    bars = list(parse_spot_1s(path))
    assert [bar.ts_ns for bar in bars] == [
        1_735_689_600_000_000_000,
        1_735_689_601_000_000_000,
        1_735_689_603_000_000_000,
    ]
    assert bars[0].close == 50_000.0
    assert bars[0].trade_count == 12

    stats = inspect_spot_1s(path)
    assert stats.row_count == 3
    assert stats.duplicate_count == 0
    assert stats.gap_count == 1
    assert stats.missing_seconds == 1


def test_parse_spot_1s_rejects_duplicate_and_nonmonotonic_timestamps(tmp_path: Path) -> None:
    base = 1_735_689_600_000_000
    duplicate = tmp_path / "duplicate.zip"
    duplicate.write_bytes(_zip_bytes([_row(base), _row(base)]))
    with pytest.raises(BinanceDataError, match="duplicate"):
        list(parse_spot_1s(duplicate))

    reversed_path = tmp_path / "reversed.zip"
    reversed_path.write_bytes(_zip_bytes([_row(base + 1_000_000), _row(base)]))
    with pytest.raises(BinanceDataError, match="monotonic"):
        list(parse_spot_1s(reversed_path))


def test_download_and_verify_streams_exact_zip_and_preserves_official_checksum(tmp_path: Path) -> None:
    url = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-2026-08-30.zip"
    checksum_url = f"{url}.CHECKSUM"
    payload = _zip_bytes([_row(1_735_689_600_000_000)])
    digest = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == checksum_url:
            return httpx.Response(200, text=f"{digest}  BTCUSDT-1s-2026-08-30.zip\n")
        if str(request.url) == url:
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BinanceArchiveClient(http_client=http)
    artifact = client.download_and_verify(url, checksum_url, tmp_path)

    artifact_path = tmp_path / artifact.path
    manifest_path = tmp_path / artifact.manifest_path
    assert artifact_path.read_bytes() == payload
    assert artifact.sha256 == digest
    assert artifact.metadata["official_checksum_sha256"] == digest
    assert verify_artifact(artifact_path, manifest_path)
    client.close()


def test_download_checksum_failure_never_finalizes_raw_archive(tmp_path: Path) -> None:
    url = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-2026-08-30.zip"
    checksum_url = f"{url}.CHECKSUM"
    payload = _zip_bytes([_row(1_735_689_600_000_000)])

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == checksum_url:
            return httpx.Response(200, text=f"{'0' * 64}  BTCUSDT-1s-2026-08-30.zip\n")
        if str(request.url) == url:
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    client = BinanceArchiveClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(BinanceDataError, match="checksum"):
        client.download_and_verify(url, checksum_url, tmp_path)

    assert not list((tmp_path / "raw" / "binance").rglob("*.zip")) if (tmp_path / "raw" / "binance").exists() else True
    client.close()
