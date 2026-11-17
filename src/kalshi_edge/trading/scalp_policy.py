"""Research-only executable-book intramarket scalp decisions.

This module deliberately contains NO broker client, network calls, durable
journal or order-entry method. It cannot approve an actual order. A trade
signal is a research hypothesis, not an expected return or an IOC fill.

Uses the current Kalshi YES/NO *bid* books and conservative general-schedule
fee reserves. Historical Kalshi 480-second classifier skill is NOT evidence
of sub-minute price predictive skill. Live integration requires the separate
execution, replay and account-reconciliation release gates in the spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from .risk import taker_fee_reserve

D = Decimal
ONE = D("1")
MIN_FINAL_SIDE_PROBABILITY = D("0.69")
MAX_RESEARCH_SPREAD = D("0.05")
MIN_RESEARCH_BID_MOMENTUM = D("0.03")
MAX_RESEARCH_ROUNDTRIP_FRICTION_FRACTION = D("0.20")
MIN_RESEARCH_SETTLEMENT_EDGE = D("0.03")
MIN_SECONDS_TO_CLOSE_FOR_ENTRY = 90
MAX_SECONDS_FROM_FINAL_FOR_ENTRY = 480
MAX_QUOTE_AGE_NS = 8_000_000_000
MIN_OBSERVATION_WINDOW_NS = 2_000_000_000
MAX_OBSERVATION_WINDOW_NS = 15_000_000_000


def _d(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("Invalid market number")
    try:
        amount = D(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("Invalid market number") from exc
    if not amount.is_finite():
        raise ValueError("Nonfinite market number")
    return amount


def _best(rows: Any) -> tuple[Decimal, Decimal]:
    """Find actual highest backed bid. Never invent a best price/depth."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing executable order-book bids")
    levels = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("Malformed order-book bid level")
        price, shares = _d(row[0]), _d(row[1])
        if not (D("0") < price < ONE and shares > 0):
            raise ValueError("Invalid order-book price or depth")
        levels.append((price, shares))
    return max(levels, key=lambda item: item[0])


@dataclass(frozen=True)
class ScalpQuote:
    side: str
    timestamp_ns: int
    bid: Decimal
    ask: Decimal
    bid_available: Decimal
    ask_available: Decimal
    spread: Decimal


@dataclass(frozen=True)
class ScalpPosition:
    """Research basis: complete, authenticated entry debit (including fees)."""
    side: str
    quantity: Decimal
    total_paid: Decimal
    entered_at_ns: int
    peak_net_dollars: Decimal = D("0")

    def __post_init__(self) -> None:
        if (self.side not in ("UP", "DOWN")
                or not isinstance(self.quantity, D) or self.quantity <= 0
                or not self.quantity.is_finite()
                or not isinstance(self.total_paid, D) or self.total_paid <= 0
                or not self.total_paid.is_finite()
                or type(self.entered_at_ns) is not int
                or self.entered_at_ns <= 0
                or not isinstance(self.peak_net_dollars, D)
                or not self.peak_net_dollars.is_finite()):
            raise ValueError("Unverified position basis")


@dataclass(frozen=True)
class ScalpDecision:
    action: str
    reason: str
    mark_net_dollars: Decimal | None = None
    live_eligible: bool = False


def quote_from_kalshi(book: dict, side: str, *, timestamp_ns: int) -> ScalpQuote:
    """Interpret the public Kalshi YES and NO *bid* ladders.

    BUY UP crosses the NO bid: ask_UP = 1 - best_no_bid.
    SELL UP hits the YES bid: bid_UP = best_yes_bid.
    BUY DOWN crosses the YES bid: ask_DOWN = 1 - best_yes_bid.
    SELL DOWN hits the NO bid: bid_DOWN = best_no_bid.
    """
    if (side not in ("UP", "DOWN") or type(timestamp_ns) is not int
            or timestamp_ns <= 0 or not isinstance(book, dict)
            or not isinstance(book.get("orderbook_fp"), dict)):
        raise ValueError("Invalid executable market quote")
    fp = book["orderbook_fp"]
    yes, yes_size = _best(fp.get("yes_dollars"))
    no, no_size = _best(fp.get("no_dollars"))
    if side == "UP":
        bid, ask = yes, ONE - no
        bid_size, ask_size = yes_size, no_size
    else:
        bid, ask = no, ONE - yes
        bid_size, ask_size = no_size, yes_size
    # Crossed/stale/asymmetric books are not a free arbitrage signal.
    if not D("0") < bid < ask < ONE:
        raise ValueError("Inconsistent executable book")
    return ScalpQuote(
        side=side, timestamp_ns=timestamp_ns, bid=bid, ask=ask,
        bid_available=bid_size, ask_available=ask_size,
        spread=ask - bid,
    )


def _research_stop(action: str, reason: str, mark: Decimal | None = None) -> ScalpDecision:
    return ScalpDecision(action=action, reason=reason, mark_net_dollars=mark)


def entry_signal(
    history: Sequence[ScalpQuote],
    predicted_side_probability: str | float | Decimal,
    contracts: str | float | Decimal,
    seconds_remaining: int,
) -> ScalpDecision:
    """Hypothetical entry gate; NEVER authorizes a real BUY.

    The 480s model's terminal probability is used only as a safety veto,
    NOT as a sub-minute return estimate. Momentum and fraction caps are
    unoptimized research hypotheses, not a demonstrated trading advantage.
    """
    try:
        p, qty = _d(predicted_side_probability), _d(contracts)
    except ValueError:
        return _research_stop("SKIP", "INVALID_INPUT")
    if (p <= MIN_FINAL_SIDE_PROBABILITY or p > ONE or qty <= 0
            or type(seconds_remaining) is not int
            or not MIN_SECONDS_TO_CLOSE_FOR_ENTRY < seconds_remaining
            <= MAX_SECONDS_FROM_FINAL_FOR_ENTRY):
        return _research_stop("SKIP", "NO_VALID_FINAL_OR_RUNWAY")
    if not isinstance(history, (list, tuple)) or len(history) < 3:
        return _research_stop("SKIP", "INSUFFICIENT_BOOK_HISTORY")
    first, middle, last = history[-3:]
    if not all(isinstance(q, ScalpQuote) for q in (first, middle, last)):
        return _research_stop("SKIP", "INVALID_BOOK_HISTORY")
    if not (first.side == middle.side == last.side
            and first.timestamp_ns < middle.timestamp_ns < last.timestamp_ns
            and MIN_OBSERVATION_WINDOW_NS
            <= last.timestamp_ns - first.timestamp_ns
            <= MAX_OBSERVATION_WINDOW_NS):
        return _research_stop("SKIP", "STALE_OR_MIXED_BOOK_HISTORY")
    if (last.spread > MAX_RESEARCH_SPREAD
            or last.ask_available < qty or last.bid_available < qty):
        return _research_stop("SKIP", "SPREAD_OR_DEPTH")
    # The entry only identifies a short-lived order-book momentum candidate.
    # An uptick or last-trade print alone is never a trade.
    if not (first.bid < middle.bid <= last.bid
            and first.ask < middle.ask <= last.ask
            and last.bid - first.bid >= MIN_RESEARCH_BID_MOMENTUM):
        return _research_stop("SKIP", "NO_STABLE_BOOK_MOMENTUM")
    entry_fee = taker_fee_reserve(last.ask, qty)
    exit_fee = taker_fee_reserve(last.bid, qty)
    debit = last.ask * qty + entry_fee
    immediate_roundtrip_drag = last.spread * qty + entry_fee + exit_fee
    # Refuse a proposed position that would instantly breach its own
    # mark-to-liquidate stop solely from spread and reserved round-trip fees.
    # This avoids a mechanically triggered stop even without a price move.
    stop_budget = max(D("0.02"), debit * D("0.08"))
    if immediate_roundtrip_drag >= stop_budget:
        return _research_stop("SKIP", "STOP_ALREADY_BREACHED_BY_COSTS")
    if (immediate_roundtrip_drag
            > MAX_RESEARCH_ROUNDTRIP_FRICTION_FRACTION * debit):
        return _research_stop("SKIP", "ROUNDTRIP_COST_TOO_HIGH")
    # A terminal edge is NOT proof of a sub-minute scalp. This is a
    # conservative veto only, never a guarantee of rising executable bids.
    if (p - last.ask) * qty < immediate_roundtrip_drag + (
            MIN_RESEARCH_SETTLEMENT_EDGE * qty):
        return _research_stop("SKIP", "TERMINAL_EDGE_INSUFFICIENT")
    return _research_stop("RESEARCH_BUY_SIGNAL", "CAUSAL_BOOK_CANDIDATE_ONLY")



def paper_momentum_entry_signal(
    history: Sequence[ScalpQuote],
    contracts: str | float | Decimal,
    seconds_remaining: int,
) -> ScalpDecision:
    """Paper-only, bidirectional quote-momentum candidate independent of finals.

    Uses three chronological same-side public book observations, not a model
    probability, BTC microstructure estimate, or proof of future profits.
    Entry/exit costs include the paper simulator's 1-cent slip on each side.
    """
    try:
        qty = _d(contracts)
    except ValueError:
        return _research_stop("SKIP", "INVALID_INPUT")
    if (qty <= 0 or type(seconds_remaining) is not int
            or not MIN_SECONDS_TO_CLOSE_FOR_ENTRY < seconds_remaining <= 900):
        return _research_stop("SKIP", "NO_SAFE_SCALP_RUNWAY")
    if not isinstance(history, (list, tuple)) or len(history) < 3:
        return _research_stop("SKIP", "INSUFFICIENT_BOOK_HISTORY")
    first, middle, last = history[-3:]
    if not all(isinstance(q, ScalpQuote) for q in (first, middle, last)):
        return _research_stop("SKIP", "INVALID_BOOK_HISTORY")
    if (not (first.side == middle.side == last.side)
            or not (first.timestamp_ns < middle.timestamp_ns < last.timestamp_ns)
            or not MIN_OBSERVATION_WINDOW_NS
            <= last.timestamp_ns - first.timestamp_ns
            <= MAX_OBSERVATION_WINDOW_NS):
        return _research_stop("SKIP", "STALE_OR_MIXED_BOOK_HISTORY")
    if (last.spread > MAX_RESEARCH_SPREAD
            or last.ask_available < qty or last.bid_available < qty):
        return _research_stop("SKIP", "SPREAD_OR_DEPTH")
    if not (first.bid < middle.bid <= last.bid
            and first.ask < middle.ask <= last.ask
            and last.bid - first.bid >= MIN_RESEARCH_BID_MOMENTUM):
        return _research_stop("SKIP", "NO_STABLE_BOOK_MOMENTUM")
    # The paper engine books 1-cent pessimistic slip on entry and on exit.
    entry_price = last.ask + D("0.01")
    exit_bid = last.bid - D("0.01")
    if entry_price >= ONE or exit_bid <= 0:
        return _research_stop("SKIP", "NO_EXECUTABLE_SIMULATED_EXIT")
    paid = qty * entry_price + taker_fee_reserve(entry_price, qty)
    liquidation = qty * exit_bid - taker_fee_reserve(exit_bid, qty)
    immediate_drag = paid - liquidation
    if immediate_drag >= max(D("0.02"), paid * D("0.08")):
        return _research_stop("SKIP", "STOP_ALREADY_BREACHED_BY_SIMULATED_COSTS")
    if immediate_drag > MAX_RESEARCH_ROUNDTRIP_FRICTION_FRACTION * paid:
        return _research_stop("SKIP", "ROUNDTRIP_COST_TOO_HIGH")
    return _research_stop("RESEARCH_BUY_SIGNAL", "PAPER_BOOK_MOMENTUM_CANDIDATE")


def exit_signal(
    position: ScalpPosition, quote: ScalpQuote, *,
    now_ns: int, seconds_remaining: int,
    take_first_net_profit: bool = False,
) -> ScalpDecision:
    """Mark *potential* early exit at actual best bid minus fee reserve.

    A signal does not close a contract. Only authenticated, matching IOC
    SELL fill evidence could establish an actual realized P/L.
    """
    if (not isinstance(position, ScalpPosition)
            or not isinstance(quote, ScalpQuote)
            or type(now_ns) is not int or now_ns < position.entered_at_ns
            or type(seconds_remaining) is not int or seconds_remaining < 0
            or quote.side != position.side):
        return _research_stop("SKIP", "INVALID_POSITION_OR_QUOTE")
    age = now_ns - quote.timestamp_ns
    if age < 0 or age > MAX_QUOTE_AGE_NS:
        return _research_stop("EXIT_BLOCKED_STALE_QUOTE", "STALE_EXIT_BOOK")
    if quote.bid_available < position.quantity:
        return _research_stop("EXIT_BLOCKED_LIQUIDITY", "NO_FULL_DEPTH")
    fee = taker_fee_reserve(quote.bid, position.quantity)
    net = position.quantity * quote.bid - fee - position.total_paid
    # These are hypotheses in dollars relative to ACTUAL all-in paid.
    # They are not a guaranteed stop-loss and are not optimized parameters.
    stop_budget = max(D("0.02"), position.total_paid * D("0.08"))
    target = max(D("0.02"), position.total_paid * D("0.05"))
    trail = max(D("0.02"), position.total_paid * D("0.025"))
    if net <= -stop_budget:
        return _research_stop("STOP_LOSS_SIGNAL", "NET_BID_STOP", net)
    if net >= target or (take_first_net_profit and net > 0):
        return _research_stop("TAKE_PROFIT_SIGNAL", "NET_BID_PROFIT", net)
    if (position.peak_net_dollars >= target
            and position.peak_net_dollars - net >= trail):
        return _research_stop("TRAILING_STOP_SIGNAL", "NET_BID_DRAWDOWN", net)
    if (seconds_remaining <= 45
            or now_ns - position.entered_at_ns >= 180_000_000_000):
        return _research_stop("TIME_EXIT_SIGNAL", "EXPOSURE_TIME_LIMIT", net)
    return _research_stop("HOLD", "NO_EXIT_TRIGGER", net)


def readiness() -> dict[str, object]:
    """Explicit release gate, not an authorization token."""
    return {
        "status": "RESEARCH_ONLY",
        "live_orders_enabled": False,
        "assumes_fills": False,
        "requires_exchange_fill_backtest": True,
        "entry_policy": "FINAL_FILTER_PLUS_EXECUTABLE_BOOK_MOMENTUM_HYPOTHESIS",
        "exit_policy": "ACTUAL_DEBIT_AND_FEE_RESERVED_EXECUTABLE_BID",
        "risk_warning": (
            "Stops and targets are not orders, and never guarantee execution. "
            "No scalp profitability has been independently established."
        ),
    }
