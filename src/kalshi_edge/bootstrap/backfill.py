from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from ..config import CollectorSettings
from .binance_history import (
    BinanceArchiveClient,
    BinanceDataError,
    archive_urls,
    convert_spot_1s_to_parquet,
)
from .config import BootstrapSettings
from .kalshi_history import KalshiHistoricalClient
from .labels import LabelNormalizationError, normalize_market_label
from .provenance import RawArtifact, sha256_file, verify_artifact, write_manifest, write_raw_artifact


FEATURE_LOOKBACK_SECONDS = 900


class KalshiBackfillReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_count: int
    downloaded_artifacts: int
    skipped_artifacts: int
    excluded_markets: tuple[str, ...]


class BinanceBackfillReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    day_count: int
    downloaded_archives: int
    skipped_archives: int
    normalized_archives: int
    dates: tuple[str, ...]


def _canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _artifact_paths(root: Path, source: str, logical_name: str) -> tuple[Path, Path]:
    data_path = root / "raw" / source / logical_name
    manifest_path = root / "manifests" / source / f"{logical_name}.manifest.json"
    return data_path, manifest_path


def _verified_existing(root: Path, source: str, logical_name: str) -> bool:
    data_path, manifest_path = _artifact_paths(root, source, logical_name)
    data_exists = data_path.exists()
    manifest_exists = manifest_path.exists()
    if not data_exists and not manifest_exists:
        return False
    if data_exists != manifest_exists:
        raise RuntimeError(f"provenance is incomplete for existing {source}/{logical_name}")
    if not verify_artifact(data_path, manifest_path):
        raise RuntimeError(f"provenance verification failed for existing {source}/{logical_name}")
    return True


def _required_kalshi_history_available(root: Path, ticker: str) -> bool:
    complete = True
    for kind in ("trades", "candlesticks"):
        data_path, manifest_path = _artifact_paths(root, "kalshi", f"{kind}/{ticker}.json")
        data_exists = data_path.exists()
        manifest_exists = manifest_path.exists()
        if not data_exists and not manifest_exists:
            complete = False
            continue
        if data_exists != manifest_exists:
            raise BinanceDataError(
                f"Kalshi required history provenance is incomplete for {ticker}: {kind}"
            )
        if not verify_artifact(data_path, manifest_path):
            raise BinanceDataError(
                f"Kalshi required history failed provenance verification for {ticker}: {kind}"
            )
    return complete


def _read_existing_json(root: Path, source: str, logical_name: str) -> dict[str, Any]:
    data_path, _ = _artifact_paths(root, source, logical_name)
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"existing {logical_name} is not a JSON object")
    return payload


def _write(
    root: Path,
    logical_name: str,
    payload: dict[str, Any],
    *,
    source_locator: str,
    row_count: int | None,
) -> None:
    write_raw_artifact(
        root=root,
        source="kalshi",
        logical_name=logical_name,
        content=_canonical_json(payload),
        metadata={
            "source_locator": source_locator,
            "parser_version": "1",
            "row_count": row_count,
            "timestamp_unit": "source-defined; normalized during dataset build",
        },
    )


def backfill_kalshi(
    settings: CollectorSettings,
    bootstrap: BootstrapSettings,
    *,
    client: KalshiHistoricalClient | Any | None = None,
) -> KalshiBackfillReport:
    root = bootstrap.bootstrap_dir
    root.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    history = client or KalshiHistoricalClient(settings, bootstrap)
    downloaded = 0
    skipped = 0
    excluded: list[str] = []

    try:
        cutoff_name = "cutoff.json"
        if _verified_existing(root, "kalshi", cutoff_name):
            skipped += 1
        else:
            cutoff = history.get_cutoff().model_dump(mode="json")
            _write(root, cutoff_name, cutoff, source_locator="/historical/cutoff", row_count=1)
            downloaded += 1

        discovered = history.discover_markets()
        tickers = sorted({str(market["ticker"]) for market in discovered if market.get("ticker")})

        for ticker in tickers:
            market_name = f"markets/{ticker}.json"
            if _verified_existing(root, "kalshi", market_name):
                market_payload = _read_existing_json(root, "kalshi", market_name)
                skipped += 1
            else:
                market_payload = history.fetch_market(ticker)
                _write(root, market_name, market_payload, source_locator=f"market/{ticker}", row_count=1)
                downloaded += 1

            try:
                label = normalize_market_label(market_payload)
            except LabelNormalizationError:
                excluded.append(ticker)
                continue

            trades_name = f"trades/{ticker}.json"
            if _verified_existing(root, "kalshi", trades_name):
                skipped += 1
            else:
                trades = history.fetch_trades(ticker)
                _write(
                    root,
                    trades_name,
                    {"ticker": ticker, "trades": trades},
                    source_locator=f"trades/{ticker}",
                    row_count=len(trades),
                )
                downloaded += 1

            candles_name = f"candlesticks/{ticker}.json"
            if _verified_existing(root, "kalshi", candles_name):
                skipped += 1
            else:
                try:
                    candles = history.fetch_candlesticks(
                        ticker,
                        start_ts=label.open_ts_ns // 1_000_000_000,
                        end_ts=label.close_ts_ns // 1_000_000_000,
                        period_interval=1,
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        excluded.append(ticker)
                        continue
                    raise
                _write(
                    root,
                    candles_name,
                    {"ticker": ticker, "candlesticks": candles},
                    source_locator=f"candlesticks/{ticker}?period_interval=1",
                    row_count=len(candles),
                )
                downloaded += 1

        return KalshiBackfillReport(
            market_count=len(tickers),
            downloaded_artifacts=downloaded,
            skipped_artifacts=skipped,
            excluded_markets=tuple(excluded),
        )
    finally:
        if own_client:
            history.close()


def _dates_for_valid_kalshi_markets(root: Path) -> list[date]:
    market_root = root / "raw" / "kalshi" / "markets"
    manifest_root = root / "manifests" / "kalshi" / "markets"
    if not market_root.exists():
        raise BinanceDataError("Kalshi bootstrap market history is required before Binance backfill")

    dates: set[date] = set()
    valid_markets = 0
    for market_path in sorted(market_root.glob("*.json")):
        manifest_path = manifest_root / f"{market_path.name}.manifest.json"
        if not manifest_path.exists() or not verify_artifact(market_path, manifest_path):
            raise BinanceDataError(f"Kalshi market artifact failed provenance verification: {market_path.name}")
        payload = json.loads(market_path.read_text(encoding="utf-8"))
        try:
            label = normalize_market_label(payload)
        except LabelNormalizationError:
            continue
        if not _required_kalshi_history_available(root, label.ticker):
            continue
        valid_markets += 1
        first = datetime.fromtimestamp(
            label.open_ts_ns // 1_000_000_000 - FEATURE_LOOKBACK_SECONDS,
            tz=timezone.utc,
        ).date()
        last = datetime.fromtimestamp(label.close_ts_ns // 1_000_000_000, tz=timezone.utc).date()
        current = first
        while current <= last:
            dates.add(current)
            current += timedelta(days=1)

    if valid_markets == 0 or not dates:
        raise BinanceDataError("Kalshi bootstrap history contains no valid settled markets for Binance date derivation")
    return sorted(dates)


def _normalized_paths(root: Path, filename: str) -> tuple[Path, Path]:
    parquet_name = f"{filename[:-4]}.parquet" if filename.lower().endswith(".zip") else f"{filename}.parquet"
    data_path = root / "normalized" / "binance" / "1s" / parquet_name
    manifest_path = root / "manifests" / "binance_normalized" / "1s" / f"{parquet_name}.manifest.json"
    return data_path, manifest_path


def _normalized_verified(root: Path, filename: str) -> bool:
    data_path, manifest_path = _normalized_paths(root, filename)
    return data_path.exists() and manifest_path.exists() and verify_artifact(data_path, manifest_path)


def _write_normalized_manifest(root: Path, filename: str, raw_artifact: RawArtifact, row_count: int) -> None:
    data_path, manifest_path = _normalized_paths(root, filename)
    artifact = RawArtifact(
        path=data_path.relative_to(root),
        manifest_path=manifest_path.relative_to(root),
        sha256=sha256_file(data_path),
        source="binance_normalized",
        retrieval_ts_utc=datetime.now(timezone.utc).isoformat(),
        byte_count=data_path.stat().st_size,
        metadata={
            "source_raw_path": raw_artifact.path.as_posix(),
            "source_raw_sha256": raw_artifact.sha256,
            "parser_version": "1",
            "row_count": row_count,
            "timestamp_unit": "nanoseconds",
            "format": "parquet",
            "schema": [
                "ts_ns",
                "open",
                "high",
                "low",
                "close",
                "base_volume",
                "quote_volume",
                "trade_count",
                "taker_buy_base",
                "taker_buy_quote",
            ],
        },
    )
    write_manifest(root, artifact)


def _raw_artifact_from_existing(root: Path, filename: str) -> RawArtifact:
    data_path, manifest_path = _artifact_paths(root, "binance", f"archive/{filename}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return RawArtifact(
        path=data_path.relative_to(root),
        manifest_path=manifest_path.relative_to(root),
        sha256=str(manifest["sha256"]),
        source="binance",
        retrieval_ts_utc=str(manifest["retrieval_ts_utc"]),
        byte_count=int(manifest["byte_count"]),
        metadata=dict(manifest.get("metadata", {})),
    )


def backfill_binance(
    bootstrap: BootstrapSettings,
    *,
    client: BinanceArchiveClient | Any | None = None,
    parquet_converter: Callable[[Path, Path], int] = convert_spot_1s_to_parquet,
) -> BinanceBackfillReport:
    root = bootstrap.bootstrap_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    dates = _dates_for_valid_kalshi_markets(root)
    urls = archive_urls(bootstrap.binance_symbol, dates, dataset="klines", interval="1s")
    own_client = client is None
    history = client or BinanceArchiveClient()
    downloaded = 0
    skipped = 0
    normalized = 0

    try:
        for url in urls:
            filename = Path(urlparse(url).path).name
            logical_name = f"archive/{filename}"
            if _verified_existing(root, "binance", logical_name):
                raw_artifact = _raw_artifact_from_existing(root, filename)
                skipped += 1
            else:
                raw_artifact = history.download_and_verify(url, f"{url}.CHECKSUM", root)
                downloaded += 1

            if _normalized_verified(root, filename):
                continue
            raw_path = root / raw_artifact.path
            normalized_path, _ = _normalized_paths(root, filename)
            row_count = parquet_converter(raw_path, normalized_path)
            if not normalized_path.exists():
                raise BinanceDataError(f"Parquet converter did not produce output for {filename}")
            _write_normalized_manifest(root, filename, raw_artifact, row_count)
            normalized += 1

        return BinanceBackfillReport(
            day_count=len(dates),
            downloaded_archives=downloaded,
            skipped_archives=skipped,
            normalized_archives=normalized,
            dates=tuple(day.isoformat() for day in dates),
        )
    finally:
        if own_client:
            history.close()