"""Read-only, immutable first-stage KXGOLD15M contract/access evidence.

Does not construct settlement labels, train models, or authorize trading.
The exact Pyth symbol is an identity assertion, NOT evidence of feed access.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

REST_BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXGOLD15M"
EXPECTED_PYTH_SYMBOL = "Metal.Index.1OZGOLD/USD"
FINAL_CHECKPOINT_SECONDS = 480
QUALIFICATION_GATE = Decimal("0.69")
_MARKET_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")


class GoldContractError(ValueError):
    """The retrieved record cannot safely be attributed to gold."""


def validate_gold_series(payload: dict[str, Any]) -> dict[str, Any]:
    series = payload.get("series")
    if not isinstance(series, dict) or series.get("ticker") != SERIES_TICKER:
        raise GoldContractError("unexpected gold series ticker")
    sources = series.get("settlement_sources")
    if not isinstance(sources, list) or len(sources) != 1:
        raise GoldContractError("unexpected settlement source count")
    source = sources[0]
    if not isinstance(source, dict):
        raise GoldContractError("invalid settlement source")
    url = source.get("url")
    if not isinstance(url, str):
        raise GoldContractError("invalid settlement source URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "app.pyth.com"
            or unquote(parsed.path) != "/explore/" + EXPECTED_PYTH_SYMBOL):
        raise GoldContractError("unexpected settlement source symbol")
    return {
        "series_ticker": SERIES_TICKER,
        "pyth_symbol": EXPECTED_PYTH_SYMBOL,
        "feed_identity_verified": True,
        "feed_subscription_verified": False,
        "contract_terms_url": series.get("contract_terms_url"),
        "series_last_updated_ts": series.get("last_updated_ts"),
    }


def _payload(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise GoldContractError("Kalshi response must be an object")
    return value


def _get(client: httpx.Client, path: str, *, params: dict[str, Any] | None,
         capture_times: dict[str, int], time_key: str) -> dict[str, Any]:
    response = client.get(f"{REST_BASE}{path}", params=params)
    capture_times[time_key] = time.time_ns()
    return _payload(response)


def collect_contract_audit(
    output_root: Path, *, client: httpx.Client | None = None,
    sample_markets: int = 5,
) -> dict[str, Any]:
    """Store a versioned audit of decoded API JSON objects and receipt times.

    This is a bounded market SAMPLE, not a historical dataset. Manual rule
    verification is required even when every request succeeds.
    """
    if not 1 <= sample_markets <= 10:
        raise ValueError("sample_markets must be between 1 and 10")
    root = output_root.resolve()
    path_parts = tuple(part.lower() for part in root.parts)
    if any(path_parts[i:i + 2] == ("data", "raw") for i in range(len(path_parts) - 1)):
        raise ValueError("gold audit cannot write inside canonical Phase 1 data/raw")
    owns_client = client is None
    client = client or httpx.Client(timeout=20.0)
    times: dict[str, int] = {}
    try:
        series = _get(client, f"/series/{SERIES_TICKER}", params=None, capture_times=times, time_key="series")
        identity = validate_gold_series(series)
        markets_page = _get(
            client, "/markets", params={"series_ticker": SERIES_TICKER, "limit": 100},
            capture_times=times, time_key="markets_page",
        )
        listed = markets_page.get("markets")
        if not isinstance(listed, list):
            raise GoldContractError("market listing must contain a markets array")
        for market in listed:
            if not isinstance(market, dict) or not _MARKET_TICKER.fullmatch(str(market.get("ticker") or "")):
                raise GoldContractError("unexpected market ticker in gold listing")
        sampled = listed[:sample_markets]
        details: list[dict[str, Any]] = []
        for item in sampled:
            ticker = item["ticker"]
            detail = _get(client, f"/markets/{ticker}", params=None,
                          capture_times=times, time_key=f"market_detail:{ticker}")
            actual = detail.get("market", detail)
            if not isinstance(actual, dict) or actual.get("ticker") != ticker:
                raise GoldContractError("market detail ticker mismatch")
            details.append(detail)
        cutoff = _get(client, "/historical/cutoff", params=None,
                      capture_times=times, time_key="historical_cutoff")
    finally:
        if owns_client:
            client.close()

    flags = ["exact_pyth_access_unverified", "settlement_rules_unverified",
             "historical_feed_coverage_unverified"]
    if not listed:
        flags.append("no_markets_on_first_page")
    if any(not (d.get("market", d).get("rules_primary") or d.get("market", d).get("rules_secondary"))
           for d in details):
        flags.append("missing_rules")
    if markets_page.get("cursor"):
        flags.append("market_sample_is_paginated")
    if any(not d.get("market", d).get("close_time") for d in details):
        flags.append("missing_market_close_time")
    report = {
        **identity,
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
        "qualification_gate": str(QUALIFICATION_GATE),
        "sampled_markets": len(details),
        "market_page_truncated": bool(markets_page.get("cursor")),
        "settlement_rule_status": "REQUIRES_MANUAL_VERIFICATION",
        "ready_for_training": False,
        "ready_for_live_trading": False,
        "quality_flags": flags,
    }
    snapshot = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": REST_BASE,
        "request_received_ts_ns": times,
        "raw_responses": {
            "series": series, "markets_page": markets_page,
            "market_details": details, "cutoff": cutoff,
        },
        "audit_report": report,
    }
    content = (json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    digest = sha256(content).hexdigest()
    target_dir = root / "contract_audits"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{time.time_ns()}-{digest[:16]}.json"
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return {**report, "sha256": digest, "artifact_path": str(path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only KXGOLD15M contract audit; NEVER trades or trains")
    parser.add_argument("--output-root", type=Path, default=Path("data/gold"))
    parser.add_argument("--sample-markets", type=int, default=5)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--inspect-artifact", type=Path, default=None,
        help="inspect an existing hashed KXGOLD15M snapshot offline, without any API requests",
    )
    modes.add_argument(
        "--inventory", action="store_true",
        help="collect read-only KXGOLD15M market listings from live and historical public APIs",
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-pages-per-tier", type=int, default=5)
    args = parser.parse_args(argv)
    if args.inspect_artifact is not None:
        from .rule_inspection import inspect_contract_snapshot
        report = inspect_contract_snapshot(args.inspect_artifact)
    elif args.inventory:
        from .inventory import collect_gold_inventory
        report = collect_gold_inventory(
            args.output_root, page_size=args.page_size, max_pages_per_tier=args.max_pages_per_tier)
    else:
        report = collect_contract_audit(args.output_root, sample_markets=args.sample_markets)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
