"""Read-only, fee-inclusive reconciliation of locally journaled AutoTrades.

This module never sends or modifies orders. Do not claim exact P/L unless
all locally recorded AutoTrade fills and the official settlement can be
matched to the same market, with no additional fills on that market.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from .execution_quality import measure_execution_quality
from .fill_reconciliation import validated_fill_unit_price


MAX_PAGES = 5
MAX_FILL_MARKET_TICKERS = 100  # Kalshi's documented comma-separated ticker cap.


class FillAuditError(ValueError):
    """A safe, enumerated cause; never render raw exchange payloads."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fill_price_failure_code(exc: ValueError) -> str:
    """Classify only messages emitted by our known fill-identity validator."""
    message = str(exc)
    if message == "Exchange YES/NO fill prices are inconsistent":
        return "FILL_PRICE_INCONSISTENT"
    if message in (
        "No exchange ticker",
        "Missing exchange market",
        "Exchange fill ticker does not match AutoTrade",
    ):
        return "FILL_MARKET_MISMATCH"
    if message == "Exchange fill conflicts with journaled direction":
        return "FILL_DIRECTION_CONFLICT"
    if message == "No verifiable exchange fill action/side":
        return "FILL_IDENTITY_MISSING"
    if message in (
        "Incomplete or invalid V2 book identity",
        "Incomplete or invalid legacy fill identity",
    ):
        return "FILL_IDENTITY_INVALID"
    if message in (
        "Missing actual exchange fill price",
        "Missing, invalid or boolean exchange price",
        "Invalid exchange price",
        "Exchange price outside [0,1]",
    ):
        return "FILL_PRICE_INVALID"
    return "EXCHANGE_FILL_INVALID"


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
        return whole + "." + decimal_places.rstrip("0").ljust(4, "0")
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


def _calculate(contracts, stages, fills, result: str, ticker: str) -> str:
    if result not in ("yes", "no"):
        raise ValueError("Market not conclusively settled")
    orders = {order: (action, side, count) for order, action, side, count in stages}
    grouped = defaultdict(list)
    fill_ids = set()
    for item in fills:
        if not isinstance(item, dict):
            raise FillAuditError("FILL_RECORD_INVALID")
        order = item.get("order_id")
        if order not in orders:
            raise RuntimeError("MIXED")
        identity = item.get("fill_id")
        if identity:
            if identity in fill_ids:
                raise FillAuditError("FILL_ID_DUPLICATE")
            fill_ids.add(identity)
        grouped[order].append(item)

    net_cash = Decimal(0)
    for order, (action, side, expected_count) in orders.items():
        total_count = Decimal(0)
        for item in grouped[order]:
            # Exchange order IDs are not a substitute for confirming the fill
            # belongs to the expected action, contract side, and market.
            # The Kalshi fill API documents action=buy/sell and side=yes/no.
            try:
                price = validated_fill_unit_price(
                    item, ticker=ticker, action=action, outcome=side,
                )
            except ValueError as exc:
                raise FillAuditError(_fill_price_failure_code(exc)) from exc
            try:
                qty = _positive(item.get("count_fp"))
            except ValueError as exc:
                raise FillAuditError("FILL_QUANTITY_INVALID") from exc
            try:
                fee = _positive(item.get("fee_cost"), zero=True)
            except ValueError as exc:
                raise FillAuditError("FILL_FEE_INVALID") from exc
            total_count += qty
            principal = qty * price
            net_cash += ((-principal if action == "BUY" else principal) - fee)
        if total_count != expected_count:
            raise ValueError("Fill quantity does not reconcile with journal")
    winner = "UP" if result == "yes" else "DOWN"
    return _exact(net_cash + contracts[winner])


def _settlement_position_matches(
    contracts: dict[str, Decimal], settlement: dict, *, required: bool,
) -> bool:
    """Compare verified net AutoTrade holdings to exchange-settled holdings.

    The recent-history UI accepts older mock/legacy settlement responses without
    position fields, but an explicitly strict full-journal audit does not.
    If either field is present, both must be valid and must match exactly.
    """
    names = ("yes_count_fp", "no_count_fp")
    values = [settlement.get(name) for name in names]
    if all(value is None for value in values):
        return not required
    if any(type(value) is not str for value in values):
        return False
    try:
        return (_positive(values[0], zero=True) == contracts["UP"]
                and _positive(values[1], zero=True) == contracts["DOWN"])
    except (ValueError, KeyError):
        return False


def _journal_reason(row: dict, stages: list[dict]) -> str:
    """Classify incomplete local evidence without inferring exchange outcomes."""
    states = [s.get("status") for s in stages if isinstance(s, dict)]
    if (row.get("status") == "NO_FILL"
            and not any(s in ("FILLED", "PARTIAL") for s in states)):
        return "NO_FILL"
    if (row.get("status") in ("UNKNOWN", "ATTEMPTED")
            or any(s in ("UNKNOWN", "ATTEMPTED") for s in states)):
        return "ORDER_OUTCOME_UNKNOWN"
    if (row.get("status") == "NOT_SUBMITTED"
            or "NOT_SUBMITTED" in states):
        return "ORDER_NOT_SUBMITTED"
    if not stages:
        return "JOURNAL_STAGE_MISSING"
    return "JOURNAL_STAGE_INCOMPLETE_OR_INVALID"


def enrich_history(
    entries: list[dict], client=None, *, require_settlement_counts: bool = False,
) -> list[dict]:
    """Read-only realized P/L with evidence reason codes.

    Each market gets a conservative reason, including when exchange evidence
    is unavailable. Reason codes are diagnostics, not automatic trade signals.
    """
    result = []
    candidates = {}
    for original in entries:
        row = dict(original)
        row.update(max_payout_dollars=None, realized_pnl_dollars=None,
                   pnl_status="UNAVAILABLE", pnl_reason=None,
                   execution_quality=None)
        stages = _stages(row)
        last_buy = next((stage for stage in reversed(stages)
                         if stage.get("stage") != "REVERSAL_SELL"
                         and stage.get("status") in ("FILLED", "PARTIAL")),
                        None)
        if last_buy is not None:
            row["outcome"] = last_buy["outcome"]
            row["status"] = last_buy["status"]
            row["fill_count"] = last_buy["fill_count"]
            row["order_id"] = last_buy["order_id"]
        try:
            plan = _plan(row)
        except (ValueError, TypeError, KeyError, InvalidOperation):
            plan = None
            row["pnl_reason"] = "JOURNAL_STAGE_INCOMPLETE_OR_INVALID"
        if plan is not None:
            contracts, confirmed_stages = plan
            row["max_payout_dollars"] = _four(max(contracts.values()))
            row["pnl_status"] = "PENDING"
            row["pnl_reason"] = "SETTLEMENT_NOT_VERIFIED"
            candidates[row["ticker"]] = (row, contracts, confirmed_stages)
        elif row["pnl_reason"] is None:
            row["pnl_reason"] = _journal_reason(row, stages)
        result.append(row)

    if not candidates:
        return result
    if client is None:
        for row, _, _ in candidates.values():
            row["pnl_reason"] = "EXCHANGE_NOT_QUERIED"
        return result
    if not callable(getattr(client, "get", None)):
        for row, _, _ in candidates.values():
            row["pnl_status"] = "UNAVAILABLE"
            row["pnl_reason"] = "EXCHANGE_EVIDENCE_UNAVAILABLE"
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

        # Absent settlement is NOT synonymous with a losing or archived trade.
        # The live endpoint excludes some archived accounts/markets.
        selected = []
        for ticker, (row, _, _) in candidates.items():
            market = found.get(ticker)
            if market is None:
                row["pnl_reason"] = "SETTLEMENT_NOT_IN_LIVE_API"
            elif market.get("market_result") not in ("yes", "no"):
                row["pnl_reason"] = "SETTLEMENT_NOT_FINAL"
            else:
                row["pnl_reason"] = "FILL_EVIDENCE_PENDING"
                selected.append(ticker)

        if not selected:
            return result

        # The Kalshi fill filter accepts at most 100 market tickers at once.
        # Never trust incomplete pagination as evidence of no fills.
        fills = []
        for first in range(0, len(selected), MAX_FILL_MARKET_TICKERS):
            batch = selected[first:first + MAX_FILL_MARKET_TICKERS]
            fills.extend(_pages(client, "/portfolio/fills",
                                params={"ticker": ",".join(batch), "limit": 1000},
                                field="fills"))
        by_market = defaultdict(list)
        for item in fills:
            ticker = (item.get("market_ticker") or item.get("ticker")
                      if isinstance(item, dict) else None)
            if ticker in candidates:
                by_market[ticker].append(item)

        for ticker in selected:
            row, contracts, stages = candidates[ticker]
            market = found[ticker]
            counts = (market.get("yes_count_fp"), market.get("no_count_fp"))
            if require_settlement_counts and any(v is None for v in counts):
                row["pnl_reason"] = "SETTLEMENT_POSITION_FIELDS_MISSING"
                continue
            if not _settlement_position_matches(
                contracts, market, required=require_settlement_counts,
            ):
                row["pnl_reason"] = "SETTLEMENT_POSITION_MISMATCH"
                continue

            try:
                profit = _calculate(
                    contracts, stages, by_market.get(ticker, []),
                    market["market_result"], ticker,
                )
                quality = measure_execution_quality(
                    _stages(row), by_market.get(ticker, []), ticker,
                )
            except FillAuditError as exc:
                row["pnl_status"] = "UNAVAILABLE"
                row["pnl_reason"] = exc.code
            except RuntimeError as exc:
                row["pnl_status"] = "MIXED" if str(exc) == "MIXED" else "UNAVAILABLE"
                row["pnl_reason"] = (
                    "OTHER_ACCOUNT_FILL_IN_MARKET" if str(exc) == "MIXED"
                    else "EXCHANGE_FILL_INVALID"
                )
            except (ValueError, InvalidOperation, TypeError, KeyError) as exc:
                row["pnl_status"] = "UNAVAILABLE"
                row["pnl_reason"] = (
                    "FILL_QUANTITY_UNRECONCILED"
                    if "Fill quantity does not reconcile with journal" in str(exc)
                    else "EXCHANGE_FILL_INVALID"
                )
            else:
                # Publish financial fields only once BOTH P/L and quality
                # computation have completed without validation exceptions.
                row["realized_pnl_dollars"] = profit
                row["execution_quality"] = quality
                row["pnl_status"] = "SETTLED"
                row["pnl_reason"] = "VERIFIED"
    except Exception:
        # Keep journal rows visible; a failed GET never equals zero realized P/L.
        for row in result:
            if row["pnl_status"] == "PENDING":
                row["pnl_status"] = "UNAVAILABLE"
                row["pnl_reason"] = "EXCHANGE_EVIDENCE_UNAVAILABLE"
    return result
