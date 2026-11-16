"""Allowlisted AutoTrade decision-time preview evidence; no authenticated API.

A quote snapshot is NOT a real fill and cannot establish realized profit.
Missing/malformed telemetry returns None without changing order eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from typing import Any, Mapping


QUOTE_FIELDS = (
    "preview_received_at_ns",
    "quoted_unit_price_dollars",
    "quoted_count_fp",
    "quoted_fee_dollars",
    "quoted_total_dollars",
    "model_win_probability",
    "model_expected_net_dollars",
    "quote_gate",
)

GATES = frozenset((
    "APPROVED", "NON_POSITIVE_EDGE", "INSUFFICIENT_EDGE",
    "INVALID_QUOTE", "SELL_UNGATED",
))


# Persisted causes are intentionally safe enum values, NOT exception text.
# Raw API responses, preview IDs, account identifiers and credentials must
# never enter the durable journal or the offline diagnostic report.
AUTO_ORDER_REJECTION_CODES = frozenset((
    "INSUFFICIENT_CASH",
    "LEGACY_CASH_FRACTION_CAP",
    "DOLLAR_BUDGET_EXCEEDED",
    "QUOTE_EXPIRED",
    "MARKET_CHANGED",
    "MARKET_NOT_OPEN",
    "NO_EXECUTABLE_LIQUIDITY",
    "QUOTE_IDENTITY_CHANGED",
    "PRICE_MOVED",
    "FRESH_QUOTE_READ_FAILED",
    "FRESH_QUOTE_VALIDATION_FAILED",
    "EXCHANGE_REJECTED",
    "OTHER_DEFINITE_REJECTION",
))


def classify_auto_order_rejection(reason: str) -> str:
    """Map a definite no-accepted-order outcome to an allowlisted cause.

    A known source prefix is enough; trailing raw exception text is never
    copied into this string. Unknown messages become an opaque generic code.
    This classification affects only diagnostics, never trading eligibility.
    """
    if not isinstance(reason, str):
        return "OTHER_DEFINITE_REJECTION"
    msg = reason.strip().casefold()
    if msg.startswith("fresh autotrade quote exceeds available cash"):
        return "INSUFFICIENT_CASH"
    if msg.startswith("fresh autotrade quote exceeds 5% cash risk ceiling"):
        return "LEGACY_CASH_FRACTION_CAP"
    if msg.startswith("fresh quote exceeds autotrade dollar budget"):
        return "DOLLAR_BUDGET_EXCEEDED"
    if msg.startswith("quote expired"):
        return "QUOTE_EXPIRED"
    if msg.startswith(("market changed", "autotrade refreshed quote identity changed")):
        return "MARKET_CHANGED"
    if msg.startswith(("market is closing", "market is not open", "market closed")):
        return "MARKET_NOT_OPEN"
    if msg.startswith(("no executable quote", "insufficient liquidity",
                       "amount is smaller than one executable share",
                       "insufficient cash for the requested size including fee")):
        return "NO_EXECUTABLE_LIQUIDITY"
    if msg.startswith("autotrade refreshed quote identity changed"):
        return "QUOTE_IDENTITY_CHANGED"
    if msg.startswith(("price moved against you",
                       "fresh autotrade price worsened")):
        return "PRICE_MOVED"
    if msg.startswith("pre-submit account/quote read http"):
        return "FRESH_QUOTE_READ_FAILED"
    if msg.startswith("pre-submit validation failed"):
        return "FRESH_QUOTE_VALIDATION_FAILED"
    if msg.startswith("kalshi rejected order"):
        return "EXCHANGE_REJECTED"
    return "OTHER_DEFINITE_REJECTION"


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or type(value) not in (int, float, str, Decimal):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def sanitize_quote_snapshot(
    preview: Mapping[str, Any],
    edge: Mapping[str, Any] | None,
    *,
    preview_received_at_ns: int,
) -> dict[str, Any] | None:
    """Extract *only* fixed-point numeric quote fields (never a preview token).

    Does not affect the trading gate. Invalid telemetry is represented by
    NULLs in an existing stage, never by an unverified claim or a trade veto.
    """
    if type(preview_received_at_ns) is not int or preview_received_at_ns <= 0:
        return None
    if not isinstance(preview, Mapping):
        return None

    price = _dec(preview.get("unit_price"))
    count = _dec(preview.get("count"))
    fee = _dec(preview.get("fee_estimate"))
    total = _dec(preview.get("amount_estimate"))
    if any(v is None for v in (price, count, fee, total)):
        return None
    if not (Decimal("0") < price <= 1 and count > 0
            and fee >= 0 and total > 0):
        return None
    if total + Decimal("0.0001") < price * count + fee:
        return None

    info = edge if isinstance(edge, Mapping) else {}
    reason = info.get("reason", "SELL_UNGATED")
    if reason not in GATES:
        return None
    win = _dec(info.get("model_win_probability"))
    expected = _dec(info.get("expected_net_dollars"))
    if win is not None and not 0 <= win <= 1:
        return None
    return {
        "preview_received_at_ns": preview_received_at_ns,
        "quoted_unit_price_dollars": str(preview["unit_price"]),
        "quoted_count_fp": str(preview["count"]),
        "quoted_fee_dollars": str(preview["fee_estimate"]),
        "quoted_total_dollars": str(preview["amount_estimate"]),
        "model_win_probability": str(info["model_win_probability"])
        if win is not None else None,
        "model_expected_net_dollars": str(info["expected_net_dollars"])
        if expected is not None else None,
        "quote_gate": reason,
    }


def read_execution_audit(path: Path) -> dict[str, Any]:
    """Offline scan of local journal, no Kalshi credentials or HTTP requests."""
    path = Path(path)
    if not path.is_file():
        return {
            "submitted_stage_attempts": 0,
            "stages_with_quoted_price": 0,
            "stages_missing_quote": 0,
            "rejection_observations": 0,
            "rejection_reasons": {},
            "unsubmitted_stage_attempts": 0,
            "unsubmitted_reason_counts": {},
            "verified_profit_dollars": None,
            "note": "Journal missing; no events can be inferred.",
        }

    # No schema mutations: an older journal may not yet include quote fields.
    with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "auto_trade_stages" not in tables:
            stages = []
        else:
            columns = {r[1] for r in db.execute("PRAGMA table_info(auto_trade_stages)")}
            # A historical journal might lack *any* newly added column.
            # Use SQL NULL aliases instead of modifying its schema, so this
            # report remains read-only even on pre-migration databases.
            select = ", ".join(
                name if name in columns else f"NULL AS {name}"
                for name in (
                    "quoted_unit_price_dollars", "status",
                    "rejection_reason_code",
                )
            )
            stages = db.execute(f"SELECT {select} FROM auto_trade_stages").fetchall()
        rejections = []
        if "auto_trade_quote_rejections" in tables:
            rejections = db.execute(
                "SELECT reason,observations FROM auto_trade_quote_rejections"
            ).fetchall()

    with_quote = sum(
        1 for r in stages if r is not None
        and r["quoted_unit_price_dollars"] is not None
    )
    reasons = Counter()
    for r in rejections:
        reasons[r["reason"]] += int(r["observations"])
    unsubmitted = [
        row for row in stages if row["status"] == "NOT_SUBMITTED"
    ]
    unsubmitted_reasons = Counter()
    for row in unsubmitted:
        code = row["rejection_reason_code"]
        if code is None:
            code = "OLDER_JOURNAL_REASON_UNAVAILABLE"
        elif code not in AUTO_ORDER_REJECTION_CODES:
            # Never echo an untrusted value from a potentially damaged DB.
            code = "UNRECOGNIZED_REASON_CODE"
        unsubmitted_reasons[code] += 1
    return {
        "report": "AUTOTRADE_EXECUTION_PREVIEW_EVIDENCE_V1",
        "submitted_stage_attempts": len(stages),
        "stages_with_quoted_price": with_quote,
        "stages_missing_quote": len(stages) - with_quote,
        "rejection_observations": sum(reasons.values()),
        "rejection_reasons": dict(sorted(reasons.items())),
        "unsubmitted_stage_attempts": len(unsubmitted),
        "unsubmitted_reason_counts": dict(sorted(unsubmitted_reasons.items())),
        "verified_profit_dollars": None,
        "note": (
            "Local order-stage quote previews and rejected modeled-edge checks only. "
            "Not exchange fills, actual paid fees, realized net P/L, or trading ROI. "
            "A skipped quote may have been sampled repeatedly; rejection counts "
            "are observations rather than unique markets. NOT_SUBMITTED "
            "causes are allowlisted codes for DEFINITE non-executions only, "
            "never proof of a filled order, missed profit, or an AutoTrade arm."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from kalshi_edge.bootstrap.config import BootstrapSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path,
                        default=BootstrapSettings().bootstrap_dir / "data"
                        / "autotrade_history.sqlite3")
    args = parser.parse_args(argv)
    print(json.dumps(read_execution_audit(args.db), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
