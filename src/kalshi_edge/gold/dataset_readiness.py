"""Gold point-in-time evidence ledger: no unsupported historical training rows.

This audits existing immutable inventories, retrospective market YES-price probes
and prospective quote receipts. The current source families cannot establish
independently reconstructed Pyth settlement outcomes or validate contract-time
semantics. The output therefore ALWAYS has zero eligible training rows.

SHA-256 here verifies file consistency, not independent source authenticity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Sequence

from .audit import SERIES_TICKER, EXPECTED_PYTH_SYMBOL, FINAL_CHECKPOINT_SECONDS
from .candle_probe import _load_inventory, GoldCandleProbeError
from .prospective import (
    GoldCaptureError, _checked_gold_root, _read_hash_snapshot,
    _write_immutable, inspect_gold_quote_snapshot,
)


class GoldDatasetError(ValueError):
    """Reject a damaged source, unsupported training claim or evidence mismatch."""


def _inventory_records(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    markets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pages = document.get("pages")
    if not isinstance(pages, dict) or set(pages) != {"historical", "live"}:
        raise GoldDatasetError("gold inventory page tiers missing")
    for tier in ("historical", "live"):
        if not isinstance(pages[tier], list):
            raise GoldDatasetError("invalid inventory pages")
        for page in pages[tier]:
            if not isinstance(page, dict) or not isinstance(page.get("response"), dict):
                raise GoldDatasetError("invalid inventory page response")
            params = page.get("params")
            if not isinstance(params, dict) or params.get("series_ticker") != SERIES_TICKER:
                raise GoldDatasetError("inventory page series mismatch")
            records = page["response"].get("markets")
            if not isinstance(records, list):
                raise GoldDatasetError("inventory markets array missing")
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get("ticker"), str):
                    raise GoldDatasetError("invalid inventory market")
                if not record["ticker"].startswith(SERIES_TICKER + "-"):
                    raise GoldDatasetError("wrong market series in inventory")
                markets[record["ticker"]].append(record)
    return markets


def _check_candle_probe(
    candle_path: Path, expected_inventory_hash: str,
) -> tuple[str, set[str], int]:
    try:
        document, digest = _read_hash_snapshot(candle_path)
    except (GoldCaptureError, OSError) as exc:
        raise GoldDatasetError(f"invalid candle probe SHA-256 or format: {exc}") from exc
    report = document.get("report")
    if (document.get("inventory_sha256") != expected_inventory_hash
            or not isinstance(report, dict)
            or report.get("inventory_sha256") != expected_inventory_hash
            or report.get("series_ticker") != SERIES_TICKER
            or report.get("reference_pyth_symbol_not_collected") != EXPECTED_PYTH_SYMBOL
            or report.get("checkpoint_seconds") != FINAL_CHECKPOINT_SECONDS
            or report.get("checkpoint_verified") is not False
            or report.get("point_in_time_publication_verified") is not False
            or report.get("settlement_oracle_verified") is not False
            or report.get("ready_for_training") is not False):
        raise GoldDatasetError("candle probe identity or readiness mismatch")
    samples = document.get("market_candles")
    if not isinstance(samples, list):
        raise GoldDatasetError("candle probe samples must be an array")
    tickers: set[str] = set()
    count = 0
    for sample in samples:
        if not isinstance(sample, dict):
            raise GoldDatasetError("candle probe sample is not an object")
        ticker = sample.get("ticker")
        checkpoint = sample.get("candidate_checkpoint_unix")
        params = sample.get("query_params")
        raw = sample.get("raw_response")
        if (not isinstance(ticker, str) or not ticker.startswith(SERIES_TICKER + "-")
                or ticker in tickers
                or not isinstance(checkpoint, int) or isinstance(checkpoint, bool)
                or not isinstance(params, dict) or params.get("end_ts") != checkpoint
                or not isinstance(raw, dict) or raw.get("ticker") != ticker
                or not isinstance(raw.get("candlesticks"), list)):
            raise GoldDatasetError("candle probe contains inconsistent market or checkpoint")
        tickers.add(ticker)
        for candle in raw["candlesticks"]:
            end = candle.get("end_period_ts") if isinstance(candle, dict) else None
            if not isinstance(end, int) or isinstance(end, bool) or end > checkpoint:
                raise GoldDatasetError("invalid or future candle in historical probe")
            count += 1
    return digest, tickers, count


def inspect_gold_dataset_audit(path: Path) -> dict[str, Any]:
    """Verify hashed audit and its fail-closed invariants before any model use."""
    try:
        doc, _ = _read_hash_snapshot(path)
    except (GoldCaptureError, OSError) as exc:
        raise GoldDatasetError(f"invalid dataset audit SHA-256: {exc}") from exc
    report = doc.get("report")
    if (doc.get("artifact_kind") != "gold_dataset_readiness_audit"
            or not isinstance(report, dict)
            or report.get("series_ticker") != SERIES_TICKER
            or report.get("settlement_symbol") != EXPECTED_PYTH_SYMBOL
            or report.get("dataset_ready") is not False
            or report.get("eligible_training_rows") != 0
            or report.get("label_status") != "ORACLE_UNVERIFIED"
            or doc.get("training_rows") != []
            or not isinstance(doc.get("source_hashes"), dict)):
        raise GoldDatasetError("dataset audit cannot claim verified training readiness")
    return doc


def audit_gold_dataset(
    inventory_path: Path,
    output_root: Path,
    *, quote_paths: Sequence[Path] = (),
    candle_probe_path: Path | None = None,
) -> dict[str, Any]:
    """Audit real source files; never turn backfilled quotes into training rows."""
    root = _checked_gold_root(output_root)
    try:
        inventory, inv_hash = _load_inventory(Path(inventory_path))
    except (GoldCandleProbeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise GoldDatasetError(f"inventory SHA-256 or identity invalid: {exc}") from exc
    records = _inventory_records(inventory)

    finalized_results = 0
    contradicting_results = 0
    for versions in records.values():
        reported = {
            str(row.get("result", "")).strip().lower() for row in versions
            if isinstance(row.get("result"), str)
            and row.get("result", "").strip().lower() in ("yes", "no")
        }
        if len(reported) > 1:
            contradicting_results += 1
        elif len(reported) == 1 and any(
            str(row.get("status", "")).lower() == "finalized" for row in versions
        ):
            finalized_results += 1

    quote_hashes: dict[str, str] = {}
    quote_market_tickers: set[str] = set()
    candidate_pre_checkpoint_receipts = 0
    for path in quote_paths:
        try:
            document = inspect_gold_quote_snapshot(Path(path))
            _, digest = _read_hash_snapshot(Path(path))
        except (GoldCaptureError, OSError) as exc:
            raise GoldDatasetError(f"quote SHA-256 or readiness invalid: {exc}") from exc
        if digest in quote_hashes.values():
            raise GoldDatasetError("duplicate prospective quote artifact")
        quote_hashes[str(path)] = digest
        for page in document["pages"]:
            received = page["response_received_ns"]
            for record in page["response"]["markets"]:
                quote_market_tickers.add(record["ticker"])
                try:
                    close = datetime.fromisoformat(record["close_time"].replace("Z", "+00:00"))
                    if close.tzinfo is None:
                        raise ValueError("naive close_time")
                    candidate_ns = int(close.timestamp() * 1_000_000_000) - (
                        FINAL_CHECKPOINT_SECONDS * 1_000_000_000)
                except (ValueError, OverflowError) as exc:
                    raise GoldDatasetError("invalid prospective quote close_time") from exc
                if received <= candidate_ns:
                    candidate_pre_checkpoint_receipts += 1

    probe_hash: str | None = None
    probe_tickers: set[str] = set()
    probe_candles = 0
    if candle_probe_path is not None:
        probe_hash, probe_tickers, probe_candles = _check_candle_probe(
            Path(candle_probe_path), inv_hash)
        if not probe_tickers.issubset(set(records)):
            raise GoldDatasetError("candle probe ticker is not in its source inventory")

    blockers = [
        "oracle_settlement_not_reconstructed",
        "contract_checkpoint_semantics_unverified",
        "no_verified_point_in_time_training_labels",
    ]
    if not quote_paths:
        blockers.append("no_prospective_quote_receipts")
    if candle_probe_path is not None:
        blockers.append("historical_candle_publication_time_unverified")
    if contradicting_results:
        blockers.append("conflicting_official_result_observations")
    report = {
        "series_ticker": SERIES_TICKER,
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "status": "BLOCKED_PENDING_ORACLE_AND_RULES",
        "dataset_ready": False,
        "eligible_training_rows": 0,
        "label_status": "ORACLE_UNVERIFIED",
        "candidate_market_count": len(set(records) | quote_market_tickers | probe_tickers),
        "inventory_market_count": len(records),
        "finalized_official_results_not_oracle_verified": finalized_results,
        "conflicting_official_result_tickers_count": contradicting_results,
        "prospective_quote_tickers": len(quote_market_tickers),
        "pre_candidate_checkpoint_receipts_not_publication_verified": candidate_pre_checkpoint_receipts,
        "historical_market_candle_count_not_as_of_verified": probe_candles,
        "checkpoint_seconds_hypothetical": FINAL_CHECKPOINT_SECONDS,
        "blockers": blockers,
        "inventory_sha256": inv_hash,
        "ready_for_live_trading": False,
    }
    source_hashes = {
        "inventory": inv_hash,
        "candle_probe": probe_hash,
        "prospective_quotes": quote_hashes,
    }
    doc = {
        "schema_version": 1, "artifact_kind": "gold_dataset_readiness_audit",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "inventory_path": str(inventory_path),
            "candle_probe_path": str(candle_probe_path) if candle_probe_path else None,
            "quote_paths": [str(p) for p in quote_paths],
        },
        "source_hashes": source_hashes,
        "candidate_market_tickers": sorted(set(records) | quote_market_tickers | probe_tickers),
        "training_rows": [], "report": report,
    }
    target, digest = _write_immutable(root, "dataset_audits", doc)
    return {**report, "sha256": digest, "artifact_path": str(target)}
