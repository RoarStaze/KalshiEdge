"""Conservative, PAPER-ONLY locked-forecast order sizing.

No function in this module places an exchange order. Exchange fee schedules may
vary by series; reserves are research estimates, not guaranteed live debit caps.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import math
from typing import Any

D = Decimal
ZERO = D("0")
ONE = D("1")
STEP = D("0.01")
MAX_STAKE = D("0.10")
DAILY_STOP = D("0.50")
MIN_EXPECTED_PROFIT = D("0.005")
MAX_ENTRY_DELAY_NS = 20_000_000_000
NS = 1_000_000_000


class EntryRejected(ValueError):
    """A safe no-trade decision; never retry the same market automatically."""


@dataclass(frozen=True)
class PaperPlan:
    ticker: str
    outcome: str
    price: str
    count: str
    fee_reserve: str
    worst_case_debit: str
    estimated_net_profit: str
    model_win_probability: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise EntryRejected("invalid number")
    try:
        x = D(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise EntryRejected("invalid number") from exc
    if not x.is_finite():
        raise EntryRejected("nonfinite number")
    return x


def taker_fee_reserve(price: Decimal, count: Decimal) -> Decimal:
    # Conservative general schedule estimate + one-cent rounding buffer.
    # Product-specific overrides may exceed this: NEVER treat as a live guarantee.
    model = D("0.07") * price * (ONE - price) * count
    return (model / STEP).to_integral_value(rounding=ROUND_CEILING) * STEP + STEP


def best_ask(book: dict, outcome: str) -> tuple[Decimal, Decimal]:
    if outcome not in ("ABOVE", "BELOW"):
        raise EntryRejected("invalid outcome")
    fp = book.get("orderbook_fp")
    if not isinstance(fp, dict):
        raise EntryRejected("missing fixed-point orderbook")
    # A NO bid at q implies a YES ask at 1-q, and conversely.
    opposite = "no_dollars" if outcome == "ABOVE" else "yes_dollars"
    levels = fp.get(opposite)
    if not isinstance(levels, list):
        raise EntryRejected("missing opposite book")
    valid = []
    for row in levels:
        try:
            price, size = decimal(row[0]), decimal(row[1])
            if ZERO < price < ONE and size >= STEP:
                valid.append((price, size))
        except (EntryRejected, IndexError, TypeError):
            continue
    if not valid:
        raise EntryRejected("no executable liquidity")
    best = max(valid, key=lambda pair: pair[0])
    return ONE - best[0], best[1]


def plan_locked_entry(
    forecast: Any, market_payload: dict, book: dict, now_ns: int
) -> PaperPlan:
    """Size one hypothetical taker fill at the displayed best ask only.

    A real order could fill partially or not at all. Does not make a live trade.
    """
    ticker = getattr(forecast, "market_ticker", "")
    if not isinstance(ticker, str) or not ticker.startswith("KXBTC15M-"):
        raise EntryRejected("unsupported market ticker")
    outcome = getattr(forecast, "forecast", "")
    if outcome not in ("ABOVE", "BELOW"):
        raise EntryRejected("SKIP forecast")
    scheduled = getattr(forecast, "scheduled_ts_ns", None)
    ts = getattr(forecast, "prediction_ts_ns", None)
    if type(now_ns) is not int or type(ts) is not int or type(scheduled) is not int:
        raise EntryRejected("invalid checkpoint timestamps")
    if not scheduled <= ts <= scheduled + 2 * NS:
        raise EntryRejected("not an immutable 240-second checkpoint")
    if not ts <= now_ns <= ts + MAX_ENTRY_DELAY_NS:
        raise EntryRejected("entry outside 20-second lock window")
    conf = decimal(getattr(forecast, "confidence", None))
    pwin = decimal(getattr(forecast, "directional_probability", None))
    if conf < D("0.69") or not (D("0.845") <= pwin <= ONE):
        raise EntryRejected("confidence below V5 production gate")
    market = market_payload.get("market")
    if not isinstance(market, dict) or market.get("ticker") != ticker:
        raise EntryRejected("market mismatch")
    if market.get("status") not in ("active", "open"):
        raise EntryRejected("market not open")
    if market.get("fractional_trading_enabled") is not True:
        raise EntryRejected("fractional trading not confirmed")
    if abs(decimal(market.get("floor_strike")) - decimal(forecast.strike)) > D("0.011"):
        raise EntryRejected("strike mismatch")
    try:
        close = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
        if close.tzinfo is None:
            raise ValueError("missing timezone")
        close_ns = int(close.timestamp() * NS)
    except (KeyError, AttributeError, ValueError, OverflowError) as exc:
        raise EntryRejected("invalid close time") from exc
    if abs(close_ns - (scheduled + 240 * NS)) > NS:
        raise EntryRejected("close time mismatch")

    price, available = best_ask(book, outcome)
    if not ZERO < price < ONE:
        raise EntryRejected("invalid ask")
    if price >= pwin:
        raise EntryRejected("no gross model edge")
    # Whole 0.01-contract increments; never round upward.
    units = min(
        int((MAX_STAKE / price / STEP).to_integral_value(rounding=ROUND_FLOOR)),
        int((available / STEP).to_integral_value(rounding=ROUND_FLOOR)),
    )
    for n in range(units, 0, -1):
        qty = D(n) * STEP
        fee = taker_fee_reserve(price, qty)
        debit = price * qty + fee
        ev = (pwin - price) * qty - fee
        if debit <= MAX_STAKE and ev >= MIN_EXPECTED_PROFIT:
            return PaperPlan(
                ticker=ticker, outcome=outcome,
                price=f"{price:.4f}", count=f"{qty:.2f}",
                fee_reserve=f"{fee:.4f}", worst_case_debit=f"{debit:.4f}",
                estimated_net_profit=f"{ev:.6f}",
                model_win_probability=f"{pwin:.6f}",
            )
    raise EntryRejected("no quantity meets fee-aware $0.10 cap and positive-edge minimum")
