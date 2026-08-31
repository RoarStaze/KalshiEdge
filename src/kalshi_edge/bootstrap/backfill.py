from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..config import CollectorSettings
from .config import BootstrapSettings
from .kalshi_history import KalshiHistoricalClient
from .labels import LabelNormalizationError, normalize_market_label
from .provenance import verify_artifact, write_raw_artifact


class KalshiBackfillReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_count: int
    downloaded_artifacts: int
    skipped_artifacts: int
    excluded_markets: tuple[str, ...]


def _canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _artifact_paths(root: Path, source: str, logical_name: str) -> tuple[Path, Path]:
    data_path = root / "raw" / source / logical_name
    manifest_path = root / "manifests" / source / f"{logical_name}.manifest.json"
    return data_path, manifest_path


def _verified_existing(root: Path, source: str, logical_name: str) -> bool:
    data_path, manifest_path = _artifact_paths(root, source, logical_name)
    return data_path.exists() and manifest_path.exists() and verify_artifact(data_path, manifest_path)


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
                candles = history.fetch_candlesticks(
                    ticker,
                    start_ts=label.open_ts_ns // 1_000_000_000,
                    end_ts=label.close_ts_ns // 1_000_000_000,
                    period_interval=1,
                )
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
