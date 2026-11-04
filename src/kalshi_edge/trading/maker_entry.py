"""Bounded, post-only maker planning and terminal-order verification.

No method here places a new order. An accepted maker order must have a
short exchange-side expiration_time and a durable pre-POST journal entry.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import time
import uuid
from typing import Callable

from .auto_edge import assess_auto_buy_edge, MAX_BANKROLL_FRACTION
from .risk import reference_nondirect_fee


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing maker quote")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid maker quote") from exc
    if not d.is_finite():
        raise ValueError("Nonfinite maker quote")
    return d


def _best_bid(rows) -> Decimal | None:
    if not isinstance(rows, list):
        return None
    bids = []
    for row in rows:
        try:
            p, qty = _decimal(row[0]), _decimal(row[1])
        except (ValueError, IndexError, TypeError):
            continue
        if Decimal("0") < p < Decimal("1") and qty > 0:
            bids.append(p)
    return max(bids) if bids else None


def build_maker_plan(
    book: dict, outcome: str, *, count: str, requested: str,
    balance: str, confidence: float, ask_price: str,
) -> dict | None:
    """Conservatively value and size a better-priced YES-book passive order.

    Uses the exchange taker fee estimate even for maker execution, so
    maker fee savings are not required to make the trade pass the value gate.
    """
    try:
        if outcome not in ("UP", "DOWN") or not isinstance(book, dict):
            return None
        fp = book.get("orderbook_fp")
        if not isinstance(fp, dict):
            return None
        qty = _decimal(count)
        cash = _decimal(balance)
        ceiling = _decimal(requested)
        taker_unit = _decimal(ask_price)
        bids = fp.get("yes_dollars" if outcome == "UP" else "no_dollars")
        maker_unit = _best_bid(bids)
        if (maker_unit is None or qty <= 0 or cash <= 0 or ceiling <= 0
                or not Decimal("0") < taker_unit < Decimal("1")
                or maker_unit >= taker_unit):
            return None
        fee = reference_nondirect_fee(maker_unit, qty)
        total = maker_unit * qty + fee
        if total <= 0 or total > min(ceiling, cash * MAX_BANKROLL_FRACTION):
            return None
        yes_price = maker_unit if outcome == "UP" else Decimal("1") - maker_unit
        preview = {
            "unit_price": format(maker_unit, ".4f"),
            "count": format(qty, ".2f"),
            "fee_estimate": format(fee, ".6f"),
            "amount_estimate": format(total, ".6f"),
        }
        edge = assess_auto_buy_edge(preview, confidence)
        if not edge["approved"]:
            return None
        return {
            **preview, "side": "bid" if outcome == "UP" else "ask",
            "price": format(yes_price, ".4f"),
            "edge_diagnostics": edge,
        }
    except (ValueError, InvalidOperation):
        return None


def _terminal_order(
    payload: dict, order_id: str, ticker: str, count: Decimal,
) -> dict | None:
    order = payload.get("order") if isinstance(payload, dict) else None
    if not isinstance(order, dict):
        raise ValueError("Missing order reconciliation")
    if order.get("ticker") != ticker:
        raise ValueError("Maker order returned wrong market")
    if order.get("order_id") not in (None, order_id):
        raise ValueError("Maker order ID mismatch")
    status = order.get("status")
    if status not in ("canceled", "cancelled", "expired", "executed"):
        return None
    filled = _decimal(order.get("fill_count_fp"))
    if not Decimal("0") <= filled <= count:
        raise ValueError("Inconsistent maker filled count")
    return {
        "status": ("FILLED" if filled == count else
                   "PARTIAL" if filled > 0 else "NO_FILL"),
        "order_id": order_id,
        "fill_count": format(filled, ".2f"),
    }


def finalize_resting_order(
    client, order_id: str, ticker: str, count: Decimal, *,
    poll_seconds: float = 14,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Poll then cancel a maker order; only verified terminal status permits retry.

    Every maker is independently expired on Kalshi before market close.
    A network/cancellation ambiguity raises rather than fabricating NO_FILL.
    """
    safe_id = str(uuid.UUID(order_id))
    if count <= 0 or poll_seconds < 0 or poll_seconds > 20:
        raise ValueError("Invalid maker order verification")
    path = f"/portfolio/orders/{safe_id}"
    deadline = time.monotonic() + poll_seconds
    while True:
        final = _terminal_order(client.get(path), safe_id, ticker, count)
        if final is not None:
            return final
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep(min(1.0, remaining))
    client.cancel_resting_order(safe_id)
    final = _terminal_order(client.get(path), safe_id, ticker, count)
    if final is None:
        raise ValueError("Maker cancellation unconfirmed; do not retry")
    return final
