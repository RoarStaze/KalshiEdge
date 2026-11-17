"""Read-only research probe of historical Kalshi gold YES-price candles.

This is NOT Pyth gold-price evidence, a verified checkpoint replay, or
training data. The market close_time-based timestamp remains hypothetical.
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
    QUALIFICATION_GATE, GoldContractError, validate_gold_series,
)

_NAME = re.compile(r"^\d+-([0-9a-f]{16})\.json$")
_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")


class GoldCandleProbeError(ValueError):
    """Stop when source identity, source timestamps, or candle windows are unsafe."""


def _load_inventory(path: Path) -> tuple[dict[str, Any], str]:
    match = _NAME.fullmatch(path.name)
    if match is None:
        raise GoldCandleProbeError("inventory filename is not SHA-256 named")
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if digest[:16] != match.group(1):
        raise GoldCandleProbeError("inventory SHA-256 mismatch")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GoldCandleProbeError("inventory JSON is invalid") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise GoldCandleProbeError("inventory schema is invalid")
    try:
        validate_gold_series(document["series"])
    except (GoldContractError, KeyError, TypeError) as exc:
        raise GoldCandleProbeError("inventory source identity is invalid") from exc
    policy = document.get("report")
    if not isinstance(policy, dict) or any((
        policy.get("series_ticker") != SERIES_TICKER,
        policy.get("checkpoint_seconds") != FINAL_CHECKPOINT_SECONDS,
        policy.get("qualification_gate") != str(QUALIFICATION_GATE),
        policy.get("ready_for_training") is not False,
    )):
        raise GoldCandleProbeError("inventory policy mismatch")
    pages = document.get("pages")
    if not isinstance(pages, dict) or set(pages) != {"historical", "live"}:
        raise GoldCandleProbeError("inventory page tiers missing")
    return document, digest


def _utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(timezone.utc) if value.tzinfo else None


def _select_markets(pages: dict[str, Any], per_tier: int) -> tuple[list[tuple[str, dict[str, Any], datetime]], int, int]:
    versions: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    tiers_by_ticker: dict[str, set[str]] = defaultdict(set)
    for tier in ("historical", "live"):
        tier_pages = pages[tier]
        if not isinstance(tier_pages, list):
            raise GoldCandleProbeError("invalid inventory pages array")
        for page in tier_pages:
            if not isinstance(page, dict) or not isinstance(page.get("response"), dict):
                raise GoldCandleProbeError("invalid inventory page")
            params = page.get("params")
            if not isinstance(params, dict) or params.get("series_ticker") != SERIES_TICKER:
                raise GoldCandleProbeError("inventory page series mismatch")
            markets = page["response"].get("markets")
            if not isinstance(markets, list):
                raise GoldCandleProbeError("invalid inventory markets array")
            for market in markets:
                if not isinstance(market, dict):
                    raise GoldCandleProbeError("invalid inventory market")
                ticker = market.get("ticker")
                if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
                    raise GoldCandleProbeError("invalid inventory gold ticker")
                versions[ticker].append((tier, market))
                tiers_by_ticker[ticker].add(tier)
    overlap = {t for t, routes in tiers_by_ticker.items() if len(routes) > 1}
    conflict = {
        ticker for ticker, rows in versions.items()
        if len({r.get("result").strip().lower() for _, r in rows
                if isinstance(r.get("result"), str) and r["result"].strip().lower() in ("yes", "no")}) > 1
    }
    buckets: dict[str, list[tuple[str, dict[str, Any], datetime]]] = {"historical": [], "live": []}
    for ticker, rows in versions.items():
        if ticker in overlap or ticker in conflict:
            continue
        tier, market = rows[-1]
        if str(market.get("status", "")).lower() != "finalized":
            continue
        if str(market.get("result", "")).lower() not in ("yes", "no"):
            continue
        close = _utc(market.get("close_time"))
        if close is None:
            continue
        buckets[tier].append((tier, market, close))
    selected: list[tuple[str, dict[str, Any], datetime]] = []
    for tier in ("historical", "live"):
        # Latest market first is a deterministic diagnostic sample, not a random test set.
        candidates = sorted(buckets[tier], key=lambda x: (x[2], x[1]["ticker"]), reverse=True)
        selected.extend(candidates[:per_tier])
    return selected, len(overlap), len(conflict)


def probe_checkpoint_candles(
    inventory_path: Path, output_root: Path, *, client: httpx.Client | None = None,
    sample_per_tier: int = 5, lookback_minutes: int = 7,
) -> dict[str, Any]:
    """Persist a bounded, immutable record of market-probability candles only."""
    if not 1 <= sample_per_tier <= 20:
        raise ValueError("sample_per_tier must be between 1 and 20")
    if not 1 <= lookback_minutes <= 100:
        raise ValueError("lookback_minutes must be between 1 and 100")
    root = Path(output_root).resolve()
    parts = tuple(part.lower() for part in root.parts)
    if any(parts[i:i + 2] == ("data", "raw") for i in range(len(parts) - 1)):
        raise ValueError("gold candle probe cannot write inside canonical Phase 1 data/raw")
    source, inventory_hash = _load_inventory(Path(inventory_path))
    chosen, overlaps, conflicts = _select_markets(source["pages"], sample_per_tier)
    if not chosen:
        raise GoldCandleProbeError("no finalized, disjoint markets with valid close_time for sampling")
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    snapshots: list[dict[str, Any]] = []
    try:
        for tier, market, close_time in chosen:
            ticker = market["ticker"]
            close_unix = int(close_time.timestamp())
            checkpoint = close_unix - FINAL_CHECKPOINT_SECONDS
            start = checkpoint - 60 * lookback_minutes
            params = {"start_ts": start, "end_ts": checkpoint, "period_interval": 1}
            if tier == "historical":
                path = f"/historical/markets/{ticker}/candlesticks"
            else:
                path = f"/series/{SERIES_TICKER}/markets/{ticker}/candlesticks"
            response = client.get(REST_BASE + path, params=params)
            received_ns = time.time_ns()
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise GoldCandleProbeError("candlestick response is not JSON") from exc
            if not isinstance(data, dict) or data.get("ticker") != ticker:
                raise GoldCandleProbeError("candlestick response ticker mismatch")
            candles = data.get("candlesticks")
            if not isinstance(candles, list):
                raise GoldCandleProbeError("candlesticks must be an array")
            seen_times = set()
            for candle in candles:
                if not isinstance(candle, dict):
                    raise GoldCandleProbeError("invalid candlestick object")
                end = candle.get("end_period_ts")
                if not isinstance(end, int) or isinstance(end, bool):
                    raise GoldCandleProbeError("invalid candlestick end_period_ts")
                if end < start or end > checkpoint:
                    raise GoldCandleProbeError("candlestick outside requested checkpoint window")
                if end in seen_times:
                    raise GoldCandleProbeError("duplicate candlestick end_period_ts")
                seen_times.add(end)
            snapshots.append({
                "ticker": ticker, "route": tier, "market_status": market.get("status"),
                "reported_result_not_verified_with_pyth": market.get("result"),
                "api_close_time_utc": close_time.isoformat(),
                "candidate_checkpoint_unix": checkpoint,
                "request_path": path, "query_params": params, "received_at_ns": received_ns,
                "raw_response": data,
            })
    finally:
        if own:
            client.close()
    total = sum(len(s["raw_response"]["candlesticks"]) for s in snapshots)
    report = {
        "series_ticker": SERIES_TICKER,
        "reference_pyth_symbol_not_collected": EXPECTED_PYTH_SYMBOL,
        "inventory_path": str(inventory_path), "inventory_sha256": inventory_hash,
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS, "qualification_gate": str(QUALIFICATION_GATE),
        "sample_per_tier": sample_per_tier, "lookback_minutes": lookback_minutes,
        "market_count": len(snapshots),
        "tiers": {t: sum(s["route"] == t for s in snapshots) for t in ("historical", "live")},
        "overlapping_market_tickers_excluded": overlaps,
        "conflicted_market_tickers_excluded": conflicts,
        "candlesticks_total": total,
        "markets_with_no_candles": sum(not s["raw_response"]["candlesticks"] for s in snapshots),
        "checkpoint_verified": False, "point_in_time_publication_verified": False,
        "settlement_oracle_verified": False, "ready_for_training": False,
        "ready_for_live_trading": False,
        "limitations": ["API close_time is not verified as actual gold settlement observation",
                        "historical candlestick end timestamps do not prove live publication by checkpoint",
                        "Kalshi market YES-price candles are not Pyth gold-price candles",
                        "sample is small and deterministically selected; no trading simulation"],
    }
    result = {
        "schema_version": 1, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_path": str(inventory_path), "inventory_sha256": inventory_hash,
        "market_candles": snapshots, "report": report,
    }
    raw = (json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    digest = sha256(raw).hexdigest()
    folder = root / "candle_probes"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{time.time_ns()}-{digest[:16]}.json"
    with target.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {**report, "sha256": digest, "artifact_path": str(target)}
