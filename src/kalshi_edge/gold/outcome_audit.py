"""Read-only accounting of Kalshi gold market result fields and lifecycle states.

Result values are observations, NOT reconstructed or verified Pyth labels.
The archived+current API records can overlap; each ticker is counted once.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .audit import (
    EXPECTED_PYTH_SYMBOL, FINAL_CHECKPOINT_SECONDS, QUALIFICATION_GATE,
    SERIES_TICKER, validate_gold_series,
)

_SNAPSHOT_NAME = re.compile(r"^\d+-([0-9a-f]{16})\.json$")
_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")
_BINARY = {"yes", "no"}


def _normalized_result(raw: Any) -> str:
    return raw.strip().lower() if isinstance(raw, str) else ""


def summarize_market_outcomes(records: Mapping[str, Sequence[dict[str, Any]]]) -> dict[str, Any]:
    """Count observed outcomes by unique ticker; retain lifecycle uncertainty.

    Status×result counts below count API RECORDS and may contain duplicates.
    All observed YES/NO counts count distinct tickers, excluding conflicts.
    """
    status_counter: Counter[str] = Counter()
    status_result: dict[str, Counter[str]] = defaultdict(Counter)
    unexpected_values: Counter[str] = Counter()
    observed_binary: Counter[str] = Counter()
    finalized_binary: Counter[str] = Counter()
    contradictory: list[str] = []
    nonfinalized_binary: list[str] = []
    finalized_without_binary: list[str] = []
    unique_statuses: dict[str, set[str]] = defaultdict(set)
    for ticker in sorted(records):
        versions = records[ticker]
        if not _TICKER.fullmatch(ticker):
            raise ValueError(f"unexpected gold market ticker {ticker}")
        result_values: set[str] = set()
        final_results: set[str] = set()
        has_nonfinal_binary = False
        has_final_without_binary = False
        for market in versions:
            if not isinstance(market, dict) or market.get("ticker") != ticker:
                raise ValueError(f"unexpected market ticker or record for {ticker}")
            raw_status = market.get("status")
            status = raw_status.strip().lower() if isinstance(raw_status, str) and raw_status.strip() else "(missing)"
            value = _normalized_result(market.get("result"))
            status_counter[status] += 1
            unique_statuses[status].add(ticker)
            if value in _BINARY:
                status_result[status][value] += 1
                result_values.add(value)
                if status == "finalized":
                    final_results.add(value)
                else:
                    has_nonfinal_binary = True
            elif value:
                unexpected_values[value] += 1
            if status == "finalized" and value not in _BINARY:
                has_final_without_binary = True
        if len(result_values) > 1:
            contradictory.append(ticker)
        elif result_values:
            observed_binary[next(iter(result_values))] += 1
            if final_results == result_values:
                finalized_binary[next(iter(result_values))] += 1
        if has_nonfinal_binary:
            nonfinalized_binary.append(ticker)
        if has_final_without_binary:
            finalized_without_binary.append(ticker)

    return {
        "observed_official_yes_results_unverified_against_pyth": observed_binary["yes"],
        "observed_official_no_results_unverified_against_pyth": observed_binary["no"],
        "finalized_yes_results_unverified_against_pyth": finalized_binary["yes"],
        "finalized_no_results_unverified_against_pyth": finalized_binary["no"],
        "contradictory_official_result_tickers": contradictory,
        "status_counts_by_record": dict(sorted(status_counter.items())),
        "unique_tickers_by_status": {s: len(ts) for s, ts in sorted(unique_statuses.items())},
        "binary_result_counts_by_status": {
            s: {"yes": count["yes"], "no": count["no"]}
            for s, count in sorted(status_result.items())
        },
        "unexpected_nonempty_result_values": dict(sorted(unexpected_values.items())),
        "nonfinalized_binary_result_ticker_count": len(nonfinalized_binary),
        "nonfinalized_binary_result_tickers": nonfinalized_binary[:20],
        "finalized_without_binary_result_ticker_count": len(finalized_without_binary),
        "finalized_without_binary_result_tickers": finalized_without_binary[:20],
    }


def inspect_inventory_snapshot(path: Path) -> dict[str, Any]:
    """Inspect the existing SHA-named inventory offline without modifying it."""
    match = _SNAPSHOT_NAME.fullmatch(path.name)
    if match is None:
        raise ValueError("inventory snapshot filename must contain SHA-256 prefix")
    data_bytes = path.read_bytes()
    digest = sha256(data_bytes).hexdigest()
    if not digest.startswith(match.group(1)):
        raise ValueError("inventory snapshot SHA-256 does not match filename")
    try:
        artifact = json.loads(data_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("inventory snapshot is not valid JSON") from exc
    if not isinstance(artifact, dict) or artifact.get("schema_version") != 1:
        raise ValueError("unknown inventory snapshot schema")
    try:
        validate_gold_series(artifact["series"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("inventory series or Pyth identity is invalid") from exc
    cutoff = artifact.get("cutoff")
    if not isinstance(cutoff, dict) or not isinstance(cutoff.get("market_settled_ts"), str):
        raise ValueError("inventory historical cutoff is missing")
    policy = artifact.get("report")
    if not isinstance(policy, dict) or (
        policy.get("checkpoint_seconds") != FINAL_CHECKPOINT_SECONDS
        or policy.get("qualification_gate") != str(QUALIFICATION_GATE)
        or policy.get("series_ticker") != SERIES_TICKER
        or policy.get("ready_for_training") is not False
    ):
        raise ValueError("inventory snapshot policy is invalid")
    pages = artifact.get("pages")
    if not isinstance(pages, dict) or set(pages) != {"historical", "live"}:
        raise ValueError("inventory must contain both historical and live pages")
    observed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    count = 0
    page_counts: dict[str, int] = {}
    for tier in ("historical", "live"):
        tier_pages = pages[tier]
        if not isinstance(tier_pages, list):
            raise ValueError(f"invalid {tier} pages")
        page_counts[tier] = len(tier_pages)
        for page in tier_pages:
            if not isinstance(page, dict) or not isinstance(page.get("response"), dict):
                raise ValueError(f"invalid {tier} page")
            params = page.get("params")
            if not isinstance(params, dict) or params.get("series_ticker") != SERIES_TICKER:
                raise ValueError(f"unexpected gold series query in {tier} page")
            markets = page["response"].get("markets")
            if not isinstance(markets, list):
                raise ValueError(f"missing {tier} markets array")
            for market in markets:
                if not isinstance(market, dict):
                    raise ValueError("invalid market record")
                ticker = market.get("ticker")
                if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
                    raise ValueError("unexpected market ticker")
                observed[ticker].append(market)
                count += 1
    outcome_report = summarize_market_outcomes(observed)
    return {
        "snapshot_path": str(path),
        "snapshot_sha256": digest,
        "series_ticker": SERIES_TICKER,
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
        "qualification_gate": str(QUALIFICATION_GATE),
        "unique_markets": len(observed),
        "total_records": count,
        "page_counts": page_counts,
        **outcome_report,
        "settlement_oracle_verified": False,
        "ready_for_training": False,
        "ready_for_live_trading": False,
        "unresolved": ["exact_pyth_candles_unavailable", "settlement_timezone_rules_unverified",
                       "historical_point_in_time_features_not_collected"],
    }
