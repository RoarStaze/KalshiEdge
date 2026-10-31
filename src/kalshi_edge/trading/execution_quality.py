"""Read-only comparison of journaled pre-order quotes to authenticated fills.

Positive adverse cost means worse execution (higher BUY cost/lower SELL
proceeds), including the difference in actual vs *estimated* fees. This
is only computed for matched, confirmed exchange fills; it never places an
order, adjusts forecasts, or substitutes a quote for realized cashflows.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


SIX = Decimal("0.000001")


def _value(v: Any, *, positive: bool = False) -> Decimal:
    if v is None or isinstance(v, bool):
        raise ValueError("Invalid or missing quote/fill amount")
    try:
        result = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid or missing quote/fill amount") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Nonfinite or nonpositive amount")
    return result


def _six(v: Decimal) -> str:
    return format(v.quantize(SIX), "f")


def measure_execution_quality(
    stages: list[dict], fills: list[dict], ticker: str,
) -> dict:
    """Return fee-aware quote-to-fill quality for confirmed stage executions.

    This is safe to call independently, but production calls it **only after**
    the stricter whole-market _calculate reconciliation succeeded.
    """
    unknown = {
        "status": "UNAVAILABLE", "stages": [],
        "stages_with_comparable_quotes": 0,
        "stages_missing_quotes": 0,
        "total_adverse_execution_dollars": None,
        "warning": "No matched, verifiable quote-to-fill comparison.",
    }
    try:
        if (not isinstance(ticker, str) or not ticker
                or not isinstance(stages, list) or not isinstance(fills, list)):
            return unknown

        confirmed = []
        for stage in stages:
            if not isinstance(stage, dict):
                return unknown
            if stage.get("status") == "NO_FILL":
                continue
            if stage.get("status") not in ("FILLED", "PARTIAL"):
                return unknown
            name = stage.get("stage")
            if name not in (
                "EARLY_BUY", "EARLY_BUY_2", "REVERSAL_SELL",
                "FINAL_BUY", "FINAL_BUY_2",
            ):
                return unknown
            order = stage.get("order_id")
            if not isinstance(order, str) or not order:
                return unknown
            side = stage.get("outcome")
            if side not in ("UP", "DOWN"):
                return unknown
            expected = _value(stage.get("fill_count"), positive=True)
            confirmed.append((stage, order, side, expected,
                              "SELL" if name == "REVERSAL_SELL" else "BUY"))
        if not confirmed:
            return unknown

        orders = {order: (stage, side, qty, action)
                  for stage, order, side, qty, action in confirmed}
        if len(orders) != len(confirmed):
            return unknown
        grouped: dict[str, list[dict]] = defaultdict(list)
        identities = set()
        for fill in fills:
            if not isinstance(fill, dict):
                return unknown
            order = fill.get("order_id")
            if order not in orders:
                return unknown
            fill_id = fill.get("fill_id")
            if fill_id:
                if fill_id in identities:
                    return unknown
                identities.add(fill_id)
            grouped[order].append(fill)

        reports = []
        missing_quote = 0
        total_adverse = Decimal("0")
        for stage, order, side, expected, action in confirmed:
            observed_count = Decimal("0")
            observed_principal = Decimal("0")
            observed_fees = Decimal("0")
            for item in grouped.get(order, []):
                observed_market = item.get("market_ticker") or item.get("ticker")
                if (observed_market != ticker
                        or (item.get("market_ticker") is not None
                            and item.get("ticker") is not None
                            and item["market_ticker"] != item["ticker"])
                        or item.get("action") != action.lower()
                        or item.get("side") != ("yes" if side == "UP" else "no")):
                    return unknown
                qty = _value(item.get("count_fp"), positive=True)
                px = _value(
                    item.get("yes_price_dollars") if side == "UP"
                    else item.get("no_price_dollars")
                )
                fee = _value(item.get("fee_cost"))
                if px < 0 or px > 1 or fee < 0:
                    return unknown
                observed_count += qty
                observed_principal += px * qty
                observed_fees += fee
            if observed_count != expected:
                return unknown

            quoted_px = stage.get("quoted_unit_price_dollars")
            quoted_qty = stage.get("quoted_count_fp")
            quoted_fee = stage.get("quoted_fee_dollars")
            if quoted_px is None or quoted_qty is None or quoted_fee is None:
                missing_quote += 1
                continue

            unit_px = _value(quoted_px)
            quote_qty = _value(quoted_qty, positive=True)
            quote_fee = _value(quoted_fee)
            if not (0 <= unit_px <= 1 and quote_fee >= 0 and expected <= quote_qty):
                return unknown

            actual_px = observed_principal / observed_count
            actual_fee_unit = observed_fees / observed_count
            estimated_fee_unit = quote_fee / quote_qty
            direction = Decimal("1") if action == "BUY" else Decimal("-1")
            adverse_unit = direction * (actual_px - unit_px) + (
                actual_fee_unit - estimated_fee_unit
            )
            adverse_total = adverse_unit * observed_count
            total_adverse += adverse_total
            reports.append({
                "stage": stage["stage"],
                "order_id": order,
                "action": action,
                "outcome": side,
                "quoted_unit_price_dollars": _six(unit_px),
                "actual_weighted_fill_price_dollars": _six(actual_px),
                "quote_count_fp": str(quote_qty),
                "filled_count_fp": str(observed_count),
                "fill_fraction_of_quote": _six(observed_count / quote_qty),
                "estimated_quote_fee_per_contract_dollars": _six(estimated_fee_unit),
                "actual_fill_fee_per_contract_dollars": _six(actual_fee_unit),
                "adverse_all_in_cost_per_contract_dollars": _six(adverse_unit),
                "adverse_total_for_filled_shares_dollars": _six(adverse_total),
            })

        if not reports:
            status = "MISSING_QUOTE"
        elif missing_quote:
            status = "PARTIAL_QUOTE_COVERAGE"
        else:
            status = "VERIFIED_QUOTE_TO_FILL"
        return {
            "status": status,
            "stages": reports,
            "stages_with_comparable_quotes": len(reports),
            "stages_missing_quotes": missing_quote,
            "total_adverse_execution_dollars": (
                _six(total_adverse) if status == "VERIFIED_QUOTE_TO_FILL" else None
            ),
            "warning": (
                "Verified exchange fills compared to stored quoted price and "
                "estimated fee for confirmed orders only. Positive adverse cost "
                "means a more expensive BUY or worse net SELL than the estimate. "
                "Not causal price impact or a forecast of future profit; "
                "missing historical quotes cannot be backfilled."
            ),
        }
    except (ValueError, InvalidOperation, TypeError, KeyError, ArithmeticError):
        return unknown
