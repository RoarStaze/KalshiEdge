"""Offline evidence report for a previously saved KXGOLD15M audit snapshot.

Reports contract rule text and timestamp anomalies, not settlement labels.
Does not modify the source snapshot or make network requests.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .audit import (
    EXPECTED_PYTH_SYMBOL, FINAL_CHECKPOINT_SECONDS, QUALIFICATION_GATE,
    GoldContractError, SERIES_TICKER, validate_gold_series,
)

_SHA_FILENAME = re.compile(r"^\d+-([0-9a-f]{16})\.json$")
_TZ_PATTERN = re.compile(r"\b(?:EST|EDT)\b", re.IGNORECASE)
_TICKER_PATTERN = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")


class RuleInspectionError(ValueError):
    """A stored gold audit cannot be safely inspected."""


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _market_details(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise RuleInspectionError("market detail must be an object")
    market = item.get("market", item)
    if not isinstance(market, dict):
        raise RuleInspectionError("market must be an object")
    ticker = market.get("ticker")
    if not isinstance(ticker, str) or not _TICKER_PATTERN.fullmatch(ticker):
        raise RuleInspectionError("unexpected market ticker")
    return market


def inspect_contract_snapshot(path: Path) -> dict[str, Any]:
    """Inspect a hashed Stage-1 snapshot without asserting verified rules.

    checkpoint_from_api_close_time_utc is derived *only* from API close_time.
    It is not an approved gold settlement observation timestamp.
    """
    match = _SHA_FILENAME.fullmatch(path.name)
    if not match:
        raise RuleInspectionError("snapshot must have SHA-256 named file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuleInspectionError("unable to read snapshot") from exc
    digest = sha256(raw).hexdigest()
    if digest[:16] != match.group(1):
        raise RuleInspectionError("snapshot SHA-256 does not match its filename")
    try:
        snapshot = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuleInspectionError("snapshot is not valid JSON") from exc
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise RuleInspectionError("unknown snapshot schema")
    responses = snapshot.get("raw_responses")
    if not isinstance(responses, dict):
        raise RuleInspectionError("missing raw responses")
    try:
        validate_gold_series(responses["series"])
    except (GoldContractError, KeyError) as exc:
        raise RuleInspectionError("unexpected gold series or source identity") from exc
    details = responses.get("market_details")
    if not isinstance(details, list):
        raise RuleInspectionError("missing market detail records")
    listing = responses.get("markets_page")
    if not isinstance(listing, dict):
        raise RuleInspectionError("missing market listing")
    audited_policy = snapshot.get("audit_report")
    if not isinstance(audited_policy, dict) or (
        audited_policy.get("checkpoint_seconds") != FINAL_CHECKPOINT_SECONDS
        or audited_policy.get("qualification_gate") != str(QUALIFICATION_GATE)
    ):
        raise RuleInspectionError("unexpected forecast policy")

    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    variants: Counter[str] = Counter()
    for item in details:
        market = _market_details(item)
        ticker = market["ticker"]
        if ticker in seen:
            raise RuleInspectionError("duplicate market ticker in snapshot")
        seen.add(ticker)
        rules_primary = market.get("rules_primary") if isinstance(market.get("rules_primary"), str) else ""
        rules_secondary = market.get("rules_secondary") if isinstance(market.get("rules_secondary"), str) else ""
        rule_text = (rules_primary + "\n" + rules_secondary).strip()
        rule_hash = sha256(rule_text.encode("utf-8")).hexdigest() if rule_text else None
        if rule_hash:
            variants[rule_hash] += 1
        abbreviations = sorted(set(name.upper() for name in _TZ_PATTERN.findall(rule_text)))
        close_time = _utc(market.get("close_time"))
        expiration_time = _utc(market.get("expiration_time"))
        flags: list[str] = []
        if not rule_text:
            flags.append("MISSING_RULES")
        if close_time is None:
            flags.append("MISSING_OR_INVALID_CLOSE_TIME")
        if len(abbreviations) > 1:
            flags.append("CONFLICTING_TZ_ABBREVIATIONS")
        if close_time is not None and expiration_time is not None and expiration_time < close_time:
            flags.append("EXPIRATION_BEFORE_TRADING_CLOSE")
        records.append({
            "ticker": ticker,
            "status": market.get("status"),
            "official_result_recorded_not_reconstructed": market.get("result"),
            "rules_primary": rules_primary,
            "rules_secondary": rules_secondary,
            "rule_text_sha256": rule_hash,
            "timezone_abbreviations": abbreviations,
            "flags": flags,
            "close_time_utc": close_time.isoformat() if close_time else None,
            "expiration_time_utc": expiration_time.isoformat() if expiration_time else None,
            "checkpoint_from_api_close_time_utc": (
                (close_time - timedelta(seconds=FINAL_CHECKPOINT_SECONDS)).isoformat() if close_time else None
            ),
            "close_time_is_settlement_observation_time_verified": False,
        })

    return {
        "snapshot_path": str(path),
        "snapshot_sha256": digest,
        "series_ticker": SERIES_TICKER,
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
        "qualification_gate": str(QUALIFICATION_GATE),
        "unique_markets": len(seen),
        "rule_variants": len(variants),
        "rule_variant_counts": dict(sorted(variants.items())),
        "market_listing_cursor_present": bool(listing.get("cursor")),
        "full_market_coverage": False,
        "settlement_verified": False,
        "training_ready": False,
        "trading_authorized": False,
        "unresolved": [
            "timezone_mapping_not_verified", "market_results_not_reconstructed",
            "pyth_settlement_candles_unavailable", "comparison_and_rounding_require_source_review",
            "close_time_is_not_verified_settlement_observation", "historical_rule_changes_not_audited",
        ],
        "markets": records,
    }
