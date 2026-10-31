"""Read-only performance summary from already reconciled AutoTrade history.

Never count pending, mixed, inconsistent, or unknown outcomes as a profit.
The input is the bounded *recent* journal; this is not lifetime or account P/L.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .autotrade_pnl import _exact

FILL_STATUSES = frozenset(("FILLED", "PARTIAL"))


def _verified_amount(value: Any) -> Decimal | None:
    # Only exchange-reconciled fixed-point strings are accepted. A float
    # would silently import binary floating point or a UI-computed estimate.
    if type(value) is not str:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def summarize_autotrade_performance(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return verified fee-inclusive statistics on the provided recent rows."""
    if not isinstance(entries, (list, tuple)):
        raise TypeError("Expected recent AutoTrade journal rows")
    tickers = [
        item.get("ticker") for item in entries
        if isinstance(item, Mapping) and isinstance(item.get("ticker"), str)
        and item["ticker"]
    ]
    counts = Counter(tickers)
    excluded_duplicates = sum(c for c in counts.values() if c > 1)

    amounts: list[Decimal] = []
    filled_unverified = other_unverified = no_fill = 0
    for row in entries:
        if not isinstance(row, Mapping):
            other_unverified += 1
            continue
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            other_unverified += 1
            continue
        if counts[ticker] > 1:
            # Reject all duplicate ticker entries, including the apparent win,
            # instead of choosing the most favorable record.
            continue
        status = row.get("status")
        if status == "NO_FILL":
            no_fill += 1
            continue
        if status not in FILL_STATUSES:
            other_unverified += 1
            continue
        value = (
            _verified_amount(row.get("realized_pnl_dollars"))
            if row.get("pnl_status") == "SETTLED" else None
        )
        if value is None:
            filled_unverified += 1
        else:
            amounts.append(value)

    gross_gains = sum((v for v in amounts if v > 0), Decimal("0"))
    gross_losses = sum((-v for v in amounts if v < 0), Decimal("0"))
    gain_count = sum(v > 0 for v in amounts)
    loss_count = sum(v < 0 for v in amounts)
    return {
        "scope": "RECENT_AUTOTRADE_JOURNAL_ONLY",
        "history_market_count": len(entries),
        "duplicate_records_excluded": excluded_duplicates,
        "verified_settled_markets": len(amounts),
        "unverified_filled_markets": filled_unverified,
        "other_unverified_markets": other_unverified,
        "no_fill_markets": no_fill,
        "gains": gain_count,
        "losses": loss_count,
        "breakeven": len(amounts) - gain_count - loss_count,
        "net_pnl_dollars": _exact(sum(amounts, Decimal("0"))) if amounts else None,
        "gross_gains_dollars": _exact(gross_gains) if amounts else None,
        "gross_losses_dollars": _exact(gross_losses) if amounts else None,
        "verified_win_rate": (
            gain_count / (gain_count + loss_count)
            if gain_count + loss_count else None
        ),
        "is_complete_for_filled_markets": (
            bool(amounts) and not filled_unverified
            and not other_unverified and not excluded_duplicates
        ),
        "warning": (
            "Verified fee-inclusive P/L of settled, reconciled AutoTrade "
            "markets in the recent local journal ONLY. Excludes unresolved "
            "or conflicting outcomes and all non-AutoTrade orders. Not "
            "lifetime P/L, not a return on investment, and not evidence "
            "of profitable future execution."
        ),
    }
