"""Immutable, read-only current + archived KXGOLD15M market inventory.

An API result is recorded as evidence; it is not an independently verified
Pyth-settlement label or a point-in-time forecasting feature.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx

from .audit import (
    REST_BASE, SERIES_TICKER, EXPECTED_PYTH_SYMBOL, FINAL_CHECKPOINT_SECONDS,
    QUALIFICATION_GATE, validate_gold_series,
)
from .outcome_audit import summarize_market_outcomes

_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")


class GoldInventoryError(ValueError):
    """Refuse unsafe market provenance, invalid API responses or pagination."""


def _json_get(client: httpx.Client, path: str, *, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    response = client.get(REST_BASE + path, params=params)
    received_at_ns = time.time_ns()
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise GoldInventoryError(f"invalid response object from {path}")
    return payload, received_at_ns


def _scan_tier(
    client: httpx.Client, *, tier: str, page_size: int, max_pages: int,
) -> tuple[list[dict[str, Any]], bool]:
    path = "/historical/markets" if tier == "historical" else "/markets"
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while len(pages) < max_pages:
        params: dict[str, Any] = {"series_ticker": SERIES_TICKER, "limit": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        payload, received_at_ns = _json_get(client, path, params=params)
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise GoldInventoryError(f"{tier} markets array is missing or invalid")
        for item in markets:
            if not isinstance(item, dict):
                raise GoldInventoryError(f"{tier} markets array contains a non-object")
            ticker = item.get("ticker")
            if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
                raise GoldInventoryError(f"unexpected ticker in {tier} inventory")
        next_cursor = payload.get("cursor")
        if next_cursor is None or next_cursor == "":
            next_cursor = None
        elif not isinstance(next_cursor, str):
            raise GoldInventoryError("malformed cursor")
        if next_cursor is not None:
            if next_cursor in seen_cursors:
                raise GoldInventoryError(f"repeated cursor in {tier} listing")
            seen_cursors.add(next_cursor)
        pages.append({"params": params, "received_at_ns": received_at_ns, "response": payload})
        if next_cursor is None:
            return pages, False
        cursor = next_cursor
    return pages, True


def collect_gold_inventory(
    output_root: Path, *, client: httpx.Client | None = None,
    page_size: int = 1000, max_pages_per_tier: int = 5,
) -> dict[str, Any]:
    """Fetch bounded, cursor-paginated public gold listings from both tiers.

    Even exhaustive pagination is not a consistent snapshot of a changing
    exchange database. Does not reconstruct the true one-minute settlement.
    """
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")
    if not 1 <= max_pages_per_tier <= 500:
        raise ValueError("max_pages_per_tier must be between 1 and 500")
    root = Path(output_root).resolve()
    parts = tuple(part.lower() for part in root.parts)
    if any(parts[i:i + 2] == ("data", "raw") for i in range(len(parts) - 1)):
        raise ValueError("gold inventory cannot write inside canonical Phase 1 data/raw")
    owns_client = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        series, series_received_at_ns = _json_get(client, f"/series/{SERIES_TICKER}")
        validate_gold_series(series)
        cutoff, cutoff_received_at_ns = _json_get(client, "/historical/cutoff")
        if not isinstance(cutoff.get("market_settled_ts"), str) or not cutoff["market_settled_ts"]:
            raise GoldInventoryError("missing market_settled_ts cutoff")
        historical_pages, historical_capped = _scan_tier(
            client, tier="historical", page_size=page_size, max_pages=max_pages_per_tier)
        live_pages, live_capped = _scan_tier(
            client, tier="live", page_size=page_size, max_pages=max_pages_per_tier)
    finally:
        if owns_client:
            client.close()

    pages = {"historical": historical_pages, "live": live_pages}
    observed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tier_tickers: dict[str, set[str]] = {"historical": set(), "live": set()}
    total_records = 0
    for tier, group in pages.items():
        for page in group:
            for market in page["response"]["markets"]:
                observed[market["ticker"]].append(market)
                tier_tickers[tier].add(market["ticker"])
                total_records += 1

    outcome_report = summarize_market_outcomes(observed)

    capped = [name for name, limit_hit in (("historical", historical_capped), ("live", live_capped))
              if limit_hit]
    report = {
        "series_ticker": SERIES_TICKER,
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
        "qualification_gate": str(QUALIFICATION_GATE),
        "page_size": page_size,
        "max_pages_per_tier": max_pages_per_tier,
        "tier_page_counts": {tier: len(group) for tier, group in pages.items()},
        "market_records": total_records,
        "unique_markets": len(observed),
        "duplicate_record_count": total_records - len(observed),
        "source_overlap_count": len(tier_tickers["historical"] & tier_tickers["live"]),
        **outcome_report,
        "page_caps_reached": capped,
        "pagination_exhausted": not bool(capped),
        "complete_for_request_window": False,
        "settlement_oracle_verified": False,
        "ready_for_training": False,
        "ready_for_live_trading": False,
        "quality_flags": ["settlement_timezone_conflict_unresolved", "missing_exact_pyth_candles",
                          "pagination_not_transactional_snapshot", "no_point_in_time_feature_provenance"],
    }
    snapshot = {
        "schema_version": 1,
        "capture_start_cutoff": cutoff,
        "api_base": REST_BASE,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "series_received_at_ns": series_received_at_ns,
        "cutoff_received_at_ns": cutoff_received_at_ns,
        "series": series,
        "cutoff": cutoff,
        "pages": pages,
        "report": report,
    }
    raw = (json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    digest = sha256(raw).hexdigest()
    directory = root / "market_inventories"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{time.time_ns()}-{digest[:16]}.json"
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {**report, "sha256": digest, "artifact_path": str(path)}
