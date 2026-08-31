from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytest

from kalshi_edge.bootstrap.backfill import backfill_binance
from kalshi_edge.bootstrap.binance_history import BinanceDataError
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.provenance import write_raw_artifact
from kalshi_edge.cli import main


def _market_payload(*, ticker: str = "KXBTC15M-TEST") -> dict:
    return {
        "market": {
            "ticker": ticker,
            "event_ticker": "KXBTC15M-EVENT",
            "market_type": "binary",
            "open_time": "2026-08-01T23:55:00Z",
            "close_time": "2026-08-02T00:10:00Z",
            "settlement_ts": "2026-08-02T00:11:00Z",
            "status": "finalized",
            "result": "yes",
            "settlement_value_dollars": "100100.2500",
            "strike_type": "greater",
            "floor_strike": 100000.0,
            "rules_primary": "Resolves Yes if the final BTC reference value is above the target price.",
            "rules_secondary": "Reference source: CF Benchmarks.",
            "is_provisional": False,
        }
    }


def _tiny_binance_zip() -> bytes:
    row = "1735689600000000,49999,50001,49998,50000,2.5,1735689600999999,125000,12,1.4,70000,0\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1s.csv", row)
    return buffer.getvalue()


class _FakeBinanceClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def download_and_verify(self, url: str, checksum_url: str, root: Path):
        self.urls.append(url)
        assert checksum_url == f"{url}.CHECKSUM"
        filename = Path(urlparse(url).path).name
        return write_raw_artifact(
            root=root,
            source="binance",
            logical_name=f"archive/{filename}",
            content=_tiny_binance_zip(),
            metadata={"source_locator": url, "official_checksum_sha256": "test"},
        )

    def close(self) -> None:
        pass


def _seed_market(root: Path, payload: dict | None = None) -> None:
    write_raw_artifact(
        root=root,
        source="kalshi",
        logical_name="markets/KXBTC15M-TEST.json",
        content=(json.dumps(payload or _market_payload(), sort_keys=True, separators=(",", ":")) + "\n").encode(),
        metadata={"source_locator": "test"},
    )


def test_backfill_binance_derives_utc_dates_from_kalshi_markets_and_is_incremental(tmp_path: Path) -> None:
    bootstrap = BootstrapSettings(bootstrap_dir=tmp_path)
    _seed_market(tmp_path)
    fake = _FakeBinanceClient()
    converted: list[tuple[Path, Path]] = []

    def converter(source: Path, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PAR1-test")
        converted.append((source, target))
        return 1

    report = backfill_binance(bootstrap, client=fake, parquet_converter=converter)
    assert report.day_count == 2
    assert report.downloaded_archives == 2
    assert report.skipped_archives == 0
    assert report.normalized_archives == 2
    assert [url.rsplit("-", 3)[-3:] for url in fake.urls]
    assert fake.urls[0].endswith("BTCUSDT-1s-2026-08-01.zip")
    assert fake.urls[1].endswith("BTCUSDT-1s-2026-08-02.zip")
    assert len(converted) == 2
    assert all(target.suffix == ".parquet" for _, target in converted)

    fake_second = _FakeBinanceClient()
    converted_second: list[tuple[Path, Path]] = []
    report_second = backfill_binance(
        bootstrap,
        client=fake_second,
        parquet_converter=lambda source, target: converted_second.append((source, target)) or 1,
    )
    assert report_second.downloaded_archives == 0
    assert report_second.skipped_archives == 2
    assert report_second.normalized_archives == 0
    assert fake_second.urls == []
    assert converted_second == []


def test_backfill_binance_includes_previous_utc_day_for_900s_feature_lookback(tmp_path: Path) -> None:
    payload = _market_payload()
    payload["market"].update(
        open_time="2026-08-02T00:05:00Z",
        close_time="2026-08-02T00:20:00Z",
        settlement_ts="2026-08-02T00:21:00Z",
    )
    _seed_market(tmp_path, payload)
    fake = _FakeBinanceClient()

    def converter(source: Path, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PAR1-test")
        return 1

    report = backfill_binance(BootstrapSettings(bootstrap_dir=tmp_path), client=fake, parquet_converter=converter)

    assert report.dates == ("2026-08-01", "2026-08-02")
    assert fake.urls[0].endswith("BTCUSDT-1s-2026-08-01.zip")
    assert fake.urls[1].endswith("BTCUSDT-1s-2026-08-02.zip")


def test_backfill_binance_requires_verified_kalshi_market_universe(tmp_path: Path) -> None:
    with pytest.raises(BinanceDataError, match="Kalshi"):
        backfill_binance(BootstrapSettings(bootstrap_dir=tmp_path), client=_FakeBinanceClient(), parquet_converter=lambda *_: 0)


def test_cli_executes_binance_backfill_without_touching_collector(monkeypatch, tmp_path: Path, capsys) -> None:
    class Report:
        def model_dump(self, mode="json"):
            return {"day_count": 1, "downloaded_archives": 1}

    monkeypatch.setenv("KALSHI_BOOTSTRAP_BOOTSTRAP_DIR", str(tmp_path))
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_binance", lambda settings: Report())
    assert main(["bootstrap-backfill", "--source", "binance"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["binance"]["day_count"] == 1
