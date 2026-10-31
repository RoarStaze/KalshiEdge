"""Read-only, fee-inclusive reconciliation of locally journaled AutoTrades.

This module never sends or modifies orders. Do not claim exact P/L unless
all locally recorded AutoTrade fills and the official settlement can be
matched to the same market, with no additional fills on that market.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


MAX_PAGES = 5


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise ValueError("Missing or invalid exchange amount") from exc
    if not result.is_finite():
        raise ValueError("Nonfinite exchange amount")
    return result


def _positive(value: Any, *, zero: bool = False) -> Decimal:
    number = _decimal(value)
    if number < 0 or (not zero and number == 0):
        raise ValueError("Invalid exchange quantity or price")
    return number


def _four(value: Decimal) -> str:
    return format(value, ".4f")


def _exact(value: Decimal) -> str:
    # Kalshi's fill prices have 4 decimal places, quantities 2, and fees 6;
    # preserve 6 decimals when needed instead of misleading cent rounding.
    if value == value.quantize(Decimal("0.01")):
        return format(value, ".2f")
    output = format(value, "f")
    if "." in output:
        whole, decimal_places = output.split(".", 1)
        return whole + "." + decimal_places.rstrip("0").ljust(2, "0")
    return output + ".00"


def _stages(row: dict) -> list[dict]:
    stages = row.get("stages")
    if isinstance(stages, list) and stages:
        return stages
    # Pre-stage-journal history contained exactly one final BUY per market.
    if row.get("status") in ("FILLED", "PARTIAL") and row.get("order_id"):
        return [{"stage": "FINAL_BUY", "outcome": row.get("outcome"),
                 "status": row["status"], "order_id": row["order_id"],
                 "fill_count": row.get("fill_count")}]
    return []


def _plan(row: dict):
    stages = _stages(row)
    if not stages:
        return None
    signed_shares = {"UP": Decimal(0), "DOWN": Decimal(0)}
    confirmed = []
    for item in stages:
        if item.get("status") in ("NO_FILL",):
            continue
        if item.get("status") not in ("FILLED", "PARTIAL"):
            return None  # Pending/unknown exchange order means no trusted payout.
        stage = item.get("stage")
        side = item.get("outcome")
        order_id = item.get("order_id")
        if (stage not in ("EARLY_BUY", "EARLY_BUY_2", "REVERSAL_SELL",
                          "FINAL_BUY", "FINAL_BUY_2")
                or side not in signed_shares or not isinstance(order_id, str)
                or not order_id):
            return None
        quantity = _positive(item.get("fill_count"))
        action = "SELL" if stage == "REVERSAL_SELL" else "BUY"
        signed_shares[side] += quantity if action == "BUY" else -quantity
        confirmed.append((order_id, action, side, quantity))
    if not confirmed or any(q < 0 for q in signed_shares.values()):
        return None
    if len(set(t[0] for t in confirmed)) != len(confirmed):
        return None
    return signed_shares, confirmed


def _pages(client, path: str, *, params: dict, field: str) -> list[dict]:
    rows = []
    cursor = ""
    seen = set()
    for _ in range(MAX_PAGES):
        request = {**params}
        if cursor:
            request["cursor"] = cursor
        result = client.get(path, params=request)
        if not isinstance(result, dict) or not isinstance(result.get(field), list):
            raise ValueError("Exchange records unavailable")
        rows.extend(result[field])
        cursor = result.get("cursor") or ""
        if not cursor:
            return rows
        if not isinstance(cursor, str) or cursor in seen:
            raise ValueError("Invalid exchange pagination")
        seen.add(cursor)
    raise ValueError("Exchange pagination incomplete; do not show profit")


def _calculate(contracts, stages, fills, result: str) -> str:
    if result not in ("yes", "no"):
        raise ValueError("Market not conclusively settled")
    orders = {order: (action, side, count) for order, action, side, count in stages}
    grouped = defaultdict(list)
    fill_ids = set()
    for item in fills:
        if not isinstance(item, dict):
            raise ValueError("Invalid exchange fill")
        order = item.get("order_id")
        if order not in orders:
            raise RuntimeError("MIXED")
        identity = item.get("fill_id")
        if identity:
            if identity in fill_ids:
                raise ValueError("Duplicate exchange fill")
            fill_ids.add(identity)
        grouped[order].append(item)

    net_cash = Decimal(0)
    for order, (action, side, expected_count) in orders.items():
        total_count = Decimal(0)
        for item in grouped[order]:
            qty = _positive(item.get("count_fp"))
            price = _positive(
                item.get("yes_price_dollars") if side == "UP"
                else item.get("no_price_dollars"), zero=True)
            fee = _positive(item.get("fee_cost"), zero=True)
            if price > 1:
                raise ValueError("Invalid fill price")
            total_count += qty
            principal = qty * price
            net_cash += ((-principal if action == "BUY" else principal) - fee)
        if total_count != expected_count:
            raise ValueError("Fill quantity does not reconcile with journal")
    winner = "UP" if result == "yes" else "DOWN"
    return _exact(net_cash + contracts[winner])


def enrich_history(entries: list[dict], client=None) -> list[dict]:
    """Add max_payout_dollars and trustworthy realized_pnl_dollars per market.

    Settlement lookup and fill audit are read-only. Missing history, mixed
    manual orders, incomplete pages and API failures never generate fake P/L.
    """
    result = []
    candidates = {}
    for original in entries:
        row = dict(original)
        row.update(max_payout_dollars=None, realized_pnl_dollars=None,
                   pnl_status="UNAVAILABLE")
        plan = _plan(row)
        if plan is not None:
            contracts, stages = plan
            row["max_payout_dollars"] = _four(max(contracts.values()))
            row["pnl_status"] = "PENDING"
            candidates[row["ticker"]] = (row, contracts, stages)
        result.append(row)
    if not candidates or client is None or not callable(getattr(client, "get", None)):
        return result
    try:
        settled = _pages(client, "/portfolio/settlements",
                         params={"limit": 1000}, field="settlements")
        found = {}
        for market in settled:
            ticker = market.get("ticker") if isinstance(market, dict) else None
            if ticker not in candidates:
                continue
            if ticker in found:
                raise ValueError("Duplicate market settlement")
            found[ticker] = market
        selected = [ticker for ticker, record in found.items()
                    if record.get("market_result") in ("yes", "no")]
        if not selected:
            return result
        fills = _pages(client, "/portfolio/fills",
                       params={"ticker": ",".join(selected), "limit": 1000},
                       field="fills")
        by_market = defaultdict(list)
        for item in fills:
            ticker = (item.get("market_ticker") or item.get("ticker")
                      if isinstance(item, dict) else None)
            if ticker in candidates:
                by_market[ticker].append(item)
        for ticker in selected:
            row, contracts, stages = candidates[ticker]
            try:
                row["realized_pnl_dollars"] = _calculate(
                    contracts, stages, by_market.get(ticker, []),
                    found[ticker]["market_result"])
                row["pnl_status"] = "SETTLED"
            except RuntimeError as exc:
                row["pnl_status"] = "MIXED" if str(exc) == "MIXED" else "UNAVAILABLE"
            except (ValueError, InvalidOperation, TypeError, KeyError):
                row["pnl_status"] = "UNAVAILABLE"
    except Exception:
        # Keep the journal visible, but never replace unavailable P/L with $0.
        for row in result:
            if row["pnl_status"] == "PENDING":
                row["pnl_status"] = "UNAVAILABLE"
    return result
