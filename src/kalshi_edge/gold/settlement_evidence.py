"""Independent gold settlement *evidence audit*, never manufactured labels.

Kalshi contract-specific start/end times, EST/EDT ambiguity, Pyth channel and
bar-label semantics are NOT independently established by the existing market
inventory and an offline local Pyth-format OHLC export. Therefore this audit
cannot turn even a matching result into a verified training label.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .audit import SERIES_TICKER, EXPECTED_PYTH_SYMBOL
from .candle_probe import GoldCandleProbeError, _load_inventory
from .dataset_readiness import _inventory_records
from .oracle_history import GoldOracleEvidenceError, inspect_pyth_history_evidence
from .prospective import GoldCaptureError, _checked_gold_root, _read_hash_snapshot, _write_immutable

_EASTERN = re.compile(r"\b(EST|EDT)\b", re.IGNORECASE)
_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")
_ALLOWED_STATUSES = {
    "CONFLICTING_KALSHI_RESULTS", "NONFINALIZED_OR_MISSING_RESULT",
    "RULES_MISSING", "CONTRACT_TIMEZONE_CONFLICT",
    "RULE_AND_CANDLE_ALIGNMENT_UNVERIFIED",
}


class GoldSettlementEvidenceError(ValueError):
    """Reject contradictory, tampered, or unauthorized settlement evidence."""


def _market_assessment(ticker: str, versions: list[dict[str, Any]]) -> dict[str, Any]:
    results = {
        str(row.get("result", "")).strip().lower()
        for row in versions
        if isinstance(row.get("result"), str)
        and row["result"].strip().lower() in ("yes", "no")
    }
    rules = {
        (str(row.get("rules_primary") or "") + "\n" + str(row.get("rules_secondary") or "")).strip()
        for row in versions
    }
    final = any(str(row.get("status") or "").lower() == "finalized" for row in versions)
    if len(results) > 1:
        status = "CONFLICTING_KALSHI_RESULTS"
    elif len(results) != 1 or not final:
        status = "NONFINALIZED_OR_MISSING_RESULT"
    elif len(rules) != 1 or not next(iter(rules)):
        status = "RULES_MISSING"
    else:
        abbreviations = set(_EASTERN.findall(next(iter(rules)).upper()))
        if abbreviations == {"EST", "EDT"}:
            status = "CONTRACT_TIMEZONE_CONFLICT"
        else:
            status = "RULE_AND_CANDLE_ALIGNMENT_UNVERIFIED"
    return {
        "ticker": ticker,
        "status": status,
        "finalized_api_result_observed_not_independently_verified": (
            next(iter(results)) if len(results) == 1 and final else None
        ),
        "rule_text_sha256": sha256(next(iter(rules)).encode()).hexdigest() if len(rules) == 1 else None,
        "timezone_abbreviations": sorted({
            z for text in rules for z in _EASTERN.findall(text.upper())
        }),
        "verified_settlement_label": None,
        "outcome_compared": False,
        "point_in_time_features_verified": False,
    }


def inspect_settlement_evidence_audit(path: Path) -> dict[str, Any]:
    """Check hashed status artifact and that it contains NO promotional claims."""
    try:
        doc, _ = _read_hash_snapshot(Path(path))
    except (GoldCaptureError, OSError) as exc:
        raise GoldSettlementEvidenceError(f"invalid settlement audit SHA-256: {exc}") from exc
    report = doc.get("report")
    if (doc.get("artifact_kind") != "gold_settlement_evidence_v12"
            or not isinstance(report, dict)
            or report.get("series_ticker") != SERIES_TICKER
            or report.get("settlement_symbol") != EXPECTED_PYTH_SYMBOL
            or report.get("settlement_verified_count") != 0
            or report.get("training_eligible_labels") != 0
            or report.get("outcome_comparisons_performed") != 0
            or report.get("dataset_ready") is not False
            or doc.get("verified_labels") != []
            or not isinstance(doc.get("source_hashes"), dict)):
        raise GoldSettlementEvidenceError("settlement audit contains unsupported verification claims")
    assessments = doc.get("market_assessments")
    if not isinstance(assessments, list):
        raise GoldSettlementEvidenceError("market assessments missing")
    seen: set[str] = set()
    for item in assessments:
        if (not isinstance(item, dict)
                or item.get("status") not in _ALLOWED_STATUSES
                or not isinstance(item.get("ticker"), str)
                or not _TICKER.fullmatch(item["ticker"])
                or item["ticker"] in seen
                or item.get("verified_settlement_label") is not None
                or item.get("outcome_compared") is not False
                or item.get("point_in_time_features_verified") is not False):
            raise GoldSettlementEvidenceError("unsupported market verification assertion")
        seen.add(item["ticker"])
    if report.get("markets_audited") != len(assessments):
        raise GoldSettlementEvidenceError("market audit count mismatch")
    if "PYTH_ORIGIN_UNATTESTED" not in report.get("blockers", ()):
        raise GoldSettlementEvidenceError("unauthenticated Pyth provenance blocker missing")
    return doc


def audit_settlement_evidence(
    inventory_path: Path, oracle_history_path: Path, output_root: Path,
) -> dict[str, Any]:
    """Check source consistency and unresolved alignment, preserve all labels as NULL."""
    root = _checked_gold_root(output_root)
    try:
        inv, inventory_sha = _load_inventory(Path(inventory_path))
    except (OSError, GoldCandleProbeError, ValueError, TypeError, KeyError) as exc:
        raise GoldSettlementEvidenceError(f"invalid inventory SHA-256 or identity: {exc}") from exc
    try:
        oracle = inspect_pyth_history_evidence(Path(oracle_history_path))
    except (GoldOracleEvidenceError, OSError) as exc:
        raise GoldSettlementEvidenceError(f"invalid Pyth evidence SHA-256 or provenance: {exc}") from exc
    records = _inventory_records(inv)
    assessments: list[dict[str, Any]] = []
    mixed = 0
    finalized = 0
    conflicts = 0
    for ticker in sorted(records):
        if not _TICKER.fullmatch(ticker):
            raise GoldSettlementEvidenceError("non-gold inventory market ticker")
        assessment = _market_assessment(ticker, records[ticker])
        assessments.append(assessment)
        if assessment["status"] == "CONTRACT_TIMEZONE_CONFLICT":
            mixed += 1
        if assessment["status"] == "CONFLICTING_KALSHI_RESULTS":
            conflicts += 1
        if assessment["finalized_api_result_observed_not_independently_verified"] is not None:
            finalized += 1

    blockers = [
        "PYTH_ORIGIN_UNATTESTED",
        "PYTH_HISTORY_CANDLE_ALIGNMENT_UNVERIFIED",
        "CONTRACT_START_END_RULE_MAPPING_UNVERIFIED",
        "PYTH_CHANNEL_FOR_KALSHI_SETTLEMENT_UNVERIFIED",
        "NO_INDEPENDENTLY_VERIFIED_SETTLEMENT_LABELS",
        "NO_AS_OF_FEATURE_PROVENANCE",
    ]
    if mixed:
        blockers.append("CONTRACT_TIMEZONE_CONFLICT")
    if conflicts:
        blockers.append("CONFLICTING_KALSHI_RESULTS")
    oracle_hash = sha256(Path(oracle_history_path).read_bytes()).hexdigest()
    report = {
        "series_ticker": SERIES_TICKER,
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "status": "BLOCKED_UNVERIFIED_ORACLE_AND_CONTRACT_TIMES",
        "markets_audited": len(assessments),
        "reported_finalized_results_not_reconstructed": finalized,
        "markets_with_mixed_EST_EDT_rule_text": mixed,
        "conflicting_result_markets": conflicts,
        "pyth_history_bars_unattested": len(oracle["raw_pyth_response"]["t"]),
        "oracle_source_authenticated": False,
        "settlement_verified_count": 0,
        "outcome_comparisons_performed": 0,
        "training_eligible_labels": 0,
        "dataset_ready": False,
        "ready_for_live_trading": False,
        "blockers": blockers,
        "limitations": [
            "A local file and SHA-256 do not authenticate Pyth Pro data",
            "Kalshi's exact per-market times cannot be inferred from a close_time alone",
            "Eastern daylight-saving discrepancies need contract-specific adjudication",
            "Pyth OHLC bar timestamp labeling and Kalshi comparison precision are not established",
            "No independent YES/NO comparison or profitability estimate is authorized",
        ],
    }
    doc = {
        "schema_version": 1,
        "artifact_kind": "gold_settlement_evidence_v12",
        "source_hashes": {
            "inventory": inventory_sha,
            "pyth_history": oracle_hash,
        },
        "source_paths": {
            "inventory": str(inventory_path),
            "pyth_history": str(oracle_history_path),
        },
        "market_assessments": assessments,
        "verified_labels": [],
        "report": report,
    }
    # Validate before writing an immutable and potentially large evidence file.
    path, digest = _write_immutable(root, "settlement_evidence", doc)
    return {**report, "artifact_path": str(path), "sha256": digest}
