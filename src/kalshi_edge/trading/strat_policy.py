"""Deterministic, model-independent KXBTC15M symmetric UP/DOWN momentum rules.

Signals use executable YES bids and asks, never last-price ticks or model
confidence. Numerical thresholds are initial experimental assumptions, not
a statistically demonstrated profitable strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

D = Decimal
MIN_REMAINING = 120
MIN_UP_ASK = D("0.65")
MAX_UP_ASK = D("0.85")
MAX_SPREAD = D("0.03")
MIN_BURST = D("0.02")
NS = 1_000_000_000


@dataclass(frozen=True)
class UpQuote:
    ticker: str
    observed_ns: int
    bid: Decimal
    ask: Decimal
    bid_depth: Decimal
    ask_depth: Decimal

    def valid(self) -> bool:
        prices = (self.bid, self.ask, self.bid_depth, self.ask_depth)
        return (self.ticker.startswith("KXBTC15M-")
                and self.ticker.replace("-", "").isalnum()
                and type(self.observed_ns) is int and self.observed_ns > 0
                and all(isinstance(x, D) and x.is_finite() for x in prices)
                and D("0") < self.bid < self.ask < D("1")
                and self.bid_depth > 0 and self.ask_depth > 0)


SideQuote = UpQuote  # Existing UP callers remain compatible.


@dataclass(frozen=True)
class MomentumResult:
    action: str
    reason: str
    speed_cents_per_second: float
    acceleration_ratio: float | None


def evaluate_side_momentum(
    quotes: Sequence[SideQuote], seconds_remaining: float, *,
    side: str, holding: bool, exit_latched: bool = False,
) -> MomentumResult:
    """The same executable-book signal logic for each side.

    Use independently sampled bid/ask series for UP and DOWN, not
    the inverse of a BTC-spot move or the inverse of the opposite bid.
    Entry evaluates a 5-sample causal window, including an advancing
    latest tick. Exit can occur after the 2-minute entry cutoff.
    """
    idle = lambda reason, speed=0., ratio=None: MomentumResult(
        "HOLD" if holding else "WAIT", reason, speed, ratio)
    if side not in ("UP", "DOWN"):
        return idle("INVALID_SIDE")
    if len(quotes) < 5:
        return idle("WARMING_UP")
    window = quotes[-5:]
    if not all(isinstance(q, UpQuote) and q.valid() for q in window):
        return idle("BAD_QUOTE")
    if len({q.ticker for q in window}) != 1:
        return idle("MARKET_CHANGED")
    times = [q.observed_ns for q in window]
    if (any(b <= a for a, b in zip(times, times[1:]))
            or times[-1] - times[0] > 12 * NS
            or times[-1] - times[0] < 2 * NS
            or max(b-a for a, b in zip(times, times[1:])) > 4 * NS):
        return idle("GAPPED_OR_STALE_SAMPLES")
    a, mid, last = window[0], window[2], window[4]
    prev_dt = D(mid.observed_ns - a.observed_ns) / NS
    now_dt = D(last.observed_ns - mid.observed_ns) / NS
    prior = (mid.bid - a.bid) / prev_dt
    recent = (last.bid - mid.bid) / now_dt
    ask_recent = (last.ask - mid.ask) / now_dt
    speed = float(recent * 100)
    ratio = float(recent / prior) if prior > 0 else None
    if holding:
        if exit_latched:
            return MomentumResult("SELL", "MOMENTUM_SLOWED_LATCHED", speed, ratio)
        if recent < 0:
            return MomentumResult("SELL", f"{side}_BID_REVERSAL", speed, ratio)
        # The last tick is the earliest available sign of slowdown; comparing
        # only 2-second averages can miss a peak and sell after a reversal.
        previous_tick = window[-2].bid - window[-3].bid
        latest_tick = last.bid - window[-2].bid
        if previous_tick > 0 and latest_tick <= previous_tick * D("0.5"):
            return MomentumResult("SELL", f"{side}_LATEST_BID_STALLED", speed, ratio)
        if prior > 0 and recent <= prior * D("0.25"):
            return MomentumResult("SELL", f"{side}_MOMENTUM_DECELERATED", speed, ratio)
        return idle(f"{side}_MOMENTUM_ACTIVE", speed, ratio)
    if seconds_remaining < MIN_REMAINING or seconds_remaining > 900:
        return idle("OUTSIDE_2_MINUTE_BUY_WINDOW", speed, ratio)
    if not MIN_UP_ASK <= last.ask <= MAX_UP_ASK:
        return idle(f"{side}_ASK_OUTSIDE_65_85", speed, ratio)
    if last.ask - last.bid > MAX_SPREAD:
        return idle("SPREAD_TOO_WIDE", speed, ratio)
    # An earlier burst may still lift the 2s average while the latest
    # orderbook is already flat. Never chase that exhausted impulse.
    if not (last.bid > window[-2].bid and last.ask > window[-2].ask):
        return idle("LATEST_TICK_NOT_RISING", speed, ratio)
    if recent * now_dt < MIN_BURST or ask_recent * now_dt < MIN_BURST:
        return idle(f"NO_FAST_{side}_BURST", speed, ratio)
    rising = a.bid <= mid.bid < last.bid and a.ask <= mid.ask < last.ask
    accelerating = prior <= 0 or recent >= prior * D("1.5")
    if not (rising and accelerating):
        return idle("NO_NEW_ACCELERATION", speed, ratio)
    return MomentumResult("BUY", f"NEW_FAST_{side}_MOMENTUM", speed, ratio)


def evaluate_up_momentum(
    quotes: Sequence[UpQuote], seconds_remaining: float, *,
    holding: bool, exit_latched: bool = False,
) -> MomentumResult:
    """Backward-compatible UP evaluation used by existing test consumers."""
    return evaluate_side_momentum(
        quotes, seconds_remaining, side="UP", holding=holding,
        exit_latched=exit_latched,
    )
