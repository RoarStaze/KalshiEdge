"""Isolated experimental 15-minute Bitcoin UP/DOWN momentum PAPER auto-trader.

Never imports authenticated order clients or submits orders. Fills are
hypothetical and include 1c per-share slippage plus fee estimates. The
four strategy rules determine entries and exits; risk limits only veto
entries. No model forecast or confidence threshold is consulted.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
import logging
import threading
import time
from typing import Callable

from .paper import PublicMarketClient
from .paper_scalper import PaperScalpLedger
from .scalp_policy import quote_from_kalshi
from .strat_policy import UpQuote, evaluate_side_momentum
from .strat_tape import StratQuoteTape

D = Decimal
NS = 1_000_000_000
LOG = logging.getLogger(__name__)
SLIPPAGE = D("0.01")
MAX_CYCLES = 12
COOLDOWN_NS = 6 * NS
INITIAL_CAPITAL = D("100.00")
SINGLE_FILL_FEE_QUANTUM = D("0.0001")  # centicent per official 2026 schedule


def standard_taker_fee_estimate(price: D, quantity: D) -> D:
    """Indicative default Kalshi taker fee, not a live-account fee quote.

    2026-07-07 standard schedule: round up cost + 0.07*qty*p*(1-p)
    to a centicent, then subtract cost. Market-specific multipliers
    and split-fill adjustments are not verified.
    """
    if (not isinstance(price, D) or not isinstance(quantity, D)
            or not price.is_finite() or not quantity.is_finite()
            or not D("0") < price < D("1") or quantity < 0):
        raise ValueError("Invalid estimated fee inputs")
    if quantity == 0:
        return D("0")
    cost = price * quantity
    raw = D("0.07") * price * (D("1") - price) * quantity
    return (cost + raw).quantize(
        SINGLE_FILL_FEE_QUANTUM, rounding=ROUND_CEILING
    ) - cost


def _cash(x: D) -> str:
    return format(x.quantize(D("0.0001")), "f")


class StratPaperTrader:
    """One UP or DOWN inventory at a time, multiple cycles per ticker."""
    def __init__(
        self, root: Path, read_live: Callable[[], dict], *,
        client=None, clock: Callable[[], int] = time.time_ns,
        settlement_reader: Callable[[str], str | None] | None = None,
    ):
        self.ledger = PaperScalpLedger(
            Path(root).resolve() / "trading" / "strat_up_paper.sqlite3",
            INITIAL_CAPITAL,
        )
        self.tape = StratQuoteTape(
            Path(root).resolve() / "trading" / "strat_quote_tape.sqlite3"
        )
        self.read_live = read_live
        self.client = client if client is not None else PublicMarketClient()
        self.clock = clock
        self.settlement_reader = settlement_reader
        self._lock = threading.RLock()
        self._enabled = False   # restart NEVER authorizes new positions
        self._paper_exploration_override = False  # explicit session-only opt-in
        self._capture_failed = False
        self._budget = D("1.00")
        self._samples: dict[str, deque[UpQuote]] = {
            side: deque(maxlen=5) for side in ("UP", "DOWN")
        }
        self._ticker: str | None = None
        self._last_exit_ns = 0
        self._exit_latched = False
        self._last_signal = "DISARMED"
        self._last_speed = 0.0
        self._last_ratio: float | None = None
        self._last_ask_cents: str | None = None
        self._last_down_ask_cents: str | None = None
        self._side_speeds = {"UP": 0.0, "DOWN": 0.0}
        self._side_reasons = {"UP": "WARMING_UP", "DOWN": "WARMING_UP"}
        self._breakeven_cents = {"UP": None, "DOWN": None}
        self._evidence_market: str | None = None
        self._entry_evidence: dict | None = None

    def _market(self, now: int) -> tuple[str, int] | None:
        try:
            live = self.read_live()
        except Exception:
            return None
        if not isinstance(live, dict):
            return None
        ticker = live.get("market_ticker")
        close = live.get("market_close_ts_ns")
        prediction = live.get("latest_prediction")
        if (not isinstance(ticker, str)
                or not ticker.startswith("KXBTC15M-")
                or not ticker.replace("-", "").isalnum()
                or type(close) is not int
                or not 0 < close - now <= 900 * NS
                or not isinstance(prediction, dict)
                or prediction.get("market_ticker") != ticker
                or type(prediction.get("prediction_ts_ns")) is not int
                or not 0 <= now - prediction["prediction_ts_ns"] <= 8 * NS):
            return None
        return ticker, close

    def _quote(self, ticker: str, now: int):
        market, book = self.client.quote(ticker)
        info = market.get("market") if isinstance(market, dict) else None
        if not isinstance(info, dict) or info.get("ticker") != ticker:
            raise ValueError("Unverified public market identity")
        if info.get("status") not in ("active", "open"):
            raise ValueError("Public market is no longer tradable")
        # REST responses can arrive after the decision loop timestamp. Use
        # client receipt time for both sides; never claim exchange event time.
        received_ns = self.clock()
        quotes = {}
        for side in ("UP", "DOWN"):
            q = quote_from_kalshi(book, side, timestamp_ns=received_ns)
            quotes[side] = UpQuote(ticker, received_ns, q.bid, q.ask,
                                   q.bid_available, q.ask_available)
        return quotes

    def _settle(self, row: dict, now: int) -> bool:
        """Use only a verified result; never infer a win from quote movement."""
        ticker = row["ticker"]
        side = None
        if self.settlement_reader is not None:
            try:
                side = self.settlement_reader(ticker)
            except Exception:
                LOG.exception("Strat official settlement lookup failed")
        if side not in ("yes", "no"):
            return False
        qty = D(row["quantity"])
        winning_side = "UP" if side == "yes" else "DOWN"
        payout = qty if row["side"] == winning_side else D("0")
        pnl = payout - D(row["total_paid"])
        self.ledger.close(
            row["id"], price=D("1") if row["side"] == winning_side else D("0"),
            fee=D("0"), now_ns=now, reason="OFFICIAL_SETTLEMENT",
            pnl=pnl, settled=True)
        self._exit_latched = False
        self._last_signal = "OFFICIALLY_SETTLED_" + row["side"] + "_" + side.upper()
        return True

    def _stats(self, now: int) -> dict:
        rows = self.ledger.recent(100000)
        done = [r for r in rows if r["status"] in ("CLOSED", "SETTLED")]
        pnl = sum((D(r["paper_net_pnl"]) for r in done), D("0"))
        today = datetime.fromtimestamp(now / NS, timezone.utc).date()
        loss = sum((
            -D(r["paper_net_pnl"]) for r in done
            if D(r["paper_net_pnl"]) < 0 and
            datetime.fromtimestamp(r["exit_ns"] / NS, timezone.utc).date() == today
        ), D("0"))
        opened = self.ledger.open_position()
        cash = self.ledger.initial_bankroll + pnl
        if opened is not None:
            cash -= D(opened["total_paid"])
        return {"pnl": pnl, "loss": loss, "open": opened, "cash": cash}

    def _market_loss_streak(self, ticker: str | None) -> int:
        """Consecutive settled/closed paper losses, scoped to one ticker."""
        if not ticker:
            return 0
        streak = 0
        for row in self.ledger.recent(100000):
            if row["ticker"] != ticker or row["status"] == "OPEN":
                continue
            if D(row["paper_net_pnl"]) >= 0:
                break
            streak += 1
        return streak

    def _global_loss_streak(self, now: int) -> int:
        """Today-UTC loss streak across ALL market tickers, not per ticker.

        Persisted closed fills are the source of truth. Restarts and the
        market's 15m boundary cannot reset a bad trading session.
        """
        day = datetime.fromtimestamp(now / NS, timezone.utc).date()
        streak = 0
        for row in self.ledger.recent(100000):
            if row["status"] == "OPEN":
                continue
            if datetime.fromtimestamp(
                    row["exit_ns"] / NS, timezone.utc).date() != day:
                break
            if D(row["paper_net_pnl"]) >= 0:
                break
            streak += 1
        return streak

    def _entry_block_reason(self, now: int, stats: dict) -> str | None:
        """Report *actual* paper entry restrictions, not directional signals.

        Historical cross-market losses are informative only; they must not
        silently veto a new experiment in a different 15-minute market.
        """
        if stats["open"] is not None:
            return "POSITION_OPEN"
        if self._capture_failed:
            return "QUOTE_TAPE_WRITE_FAILED"
        if not self._enabled:
            return "NOT_ARMED"
        if self._ticker is None:
            return "WAITING_FOR_FRESH_MARKET"
        market = self._market(now)
        if market is None or market[0] != self._ticker:
            return "WAITING_FOR_FRESH_MARKET"
        remain = (market[1] - now) / NS
        if remain < 120:
            return "OUTSIDE_2_MINUTE_BUY_WINDOW"
        if self.ledger.ticker_count(self._ticker) >= MAX_CYCLES:
            return "MAX_MARKET_CYCLES"
        if now - self._last_exit_ns < COOLDOWN_NS:
            return "REENTRY_COOLDOWN"
        if self._last_ask_cents is None or self._last_down_ask_cents is None:
            return "WAITING_FOR_EXECUTABLE_QUOTES"
        up = D(self._last_ask_cents) / D("100")
        down = D(self._last_down_ask_cents) / D("100")
        qualifying = [
            side for side, ask in (("UP", up), ("DOWN", down))
            if D("0.65") <= ask <= D("0.85")
        ]
        if not qualifying:
            return "BOTH_SIDE_ASKS_OUTSIDE_65_85"
        if not self._paper_exploration_override:
            assessed = (self._entry_evidence or {}).get("sides", {})
            blocked = [
                side for side in qualifying
                if assessed.get(side, {}).get("entry_veto") is True
            ]
            if len(blocked) == len(qualifying):
                if len(qualifying) == 1:
                    return f"NEGATIVE_HISTORICAL_{qualifying[0]}_TAKER_MARKOUT"
                return "NEGATIVE_HISTORICAL_BOTH_ELIGIBLE_SIDES"
        return None

    def status(self) -> dict:
        with self._lock:
            now = self.clock()
            stats = self._stats(now)
            position = stats["open"]
            tape = self.tape.summary()
            streak = self._global_loss_streak(now)
            return {
                "mode": "EXPERIMENTAL_PAPER_ONLY",
                "enabled": self._enabled,
                "paper_exploration_override": self._paper_exploration_override,
                "live_orders_enabled": False,
                "budget_dollars": _cash(self._budget),
                "virtual_cash_dollars": _cash(stats["cash"]),
                "paper_net_pnl_dollars": _cash(stats["pnl"]),
                "daily_paper_loss_dollars": _cash(stats["loss"]),
                "market_loss_streak": self._market_loss_streak(self._ticker),
                "global_loss_streak": streak,
                "entry_block_reason": self._entry_block_reason(now, stats),
                "quote_tape_samples": tape["snapshots"],
                "entry_evidence": self._entry_evidence,
                "quote_tape_markets": tape["markets"],
                "quote_tape_path": tape["path"],
                "quote_tape_healthy": not self._capture_failed,
                "max_same_market_loss_streak": None,
                "fee_mode": "INDICATIVE_STANDARD_TAKER_JULY_2026",
                "slippage_cents_per_leg": "1.00",
                "break_even_up_cents": self._breakeven_cents["UP"],
                "break_even_down_cents": self._breakeven_cents["DOWN"],
                "open_position": position,
                "round_trips": self.ledger.recent(25),
                "last_signal": self._last_signal,
                "speed_cents_per_second": round(self._last_speed, 3),
                "acceleration_ratio": self._last_ratio,
                "market_ticker": self._ticker,
                "up_ask_cents": self._last_ask_cents,
                "down_ask_cents": self._last_down_ask_cents,
                "side_momentum": {
                    side: {
                        "speed_cents_per_second": round(self._side_speeds[side], 3),
                        "signal": self._side_reasons[side],
                    } for side in ("UP", "DOWN")
                },
                "samples": max(len(series) for series in self._samples.values()),
                "scan_interval_seconds": 0.75,
                "max_cycles_per_market": MAX_CYCLES,
                "rules": {
                    "buy_min_remaining_seconds": 120,
                    "side_ask_cents_range": [65, 85],
                    "sides": ["UP", "DOWN"],
                    "entry": "NEW_EXECUTABLE_SIDE_ACCELERATION",
                    "exit": "HELD_SIDE_BID_MOMENTUM_DECELERATION_OR_REVERSAL",
                },
                "warning": ("Signal has no verified positive expectancy. "
                            "Cross-market loss streak is informational only; "
                            "Historical paper loss streaks and daily losses are "
                            "informational; no paper loss-streak or daily-dollar "
                            "halt prevents research entries. "
                            "Historical negative taker entry gate is bypassed "
                            "ONLY when explicitly selected for PAPER research. " 
                            "Paper only: top-of-book hypothetical fills with 1c "
                            "slippage per leg and indicative standard taker fees. "
                            "Market-specific fees and actual fills are unverified. "
                            "No demonstrated positive after-fee expectancy; "
                            "an unfilled or illiquid exit can remain open."),
            }

    def replay_audit(self) -> dict:
        """Read-only, on-demand audit of historical FUTURE executable quotes."""
        from .strat_replay import analyze_causal_markouts
        return analyze_causal_markouts(self.tape.path)

    def arm(
        self, amount: str, *,
        exploratory_ignore_negative_evidence: bool = False,
    ) -> dict:
        with self._lock:
            if type(exploratory_ignore_negative_evidence) is not bool:
                raise ValueError("Paper exploration override must be a boolean")
            try:
                budget = D(amount)
            except Exception as exc:
                raise ValueError("Invalid Strat dollar amount") from exc
            stats = self._stats(self.clock())
            if (not budget.is_finite() or budget.as_tuple().exponent < -2
                    or budget < D("0.10") or budget > D("5.00")
                    or budget > max(D("0"), stats["cash"]) * D("0.05")):
                raise ValueError("Strat budget must be $0.10–$5 and <=5% paper cash")
            self._budget = budget
            # Never persisted; each new session requires explicit opt-in.
            # This only skips the historical paper entry veto. Time, price,
            # model freshness, budget and position rules remain unchanged.
            self._paper_exploration_override = exploratory_ignore_negative_evidence
            self._capture_failed = False  # next observation retries tape write
            self._enabled = True
            self._last_signal = "SCANNING_UP_AND_DOWN_MOMENTUM"
            return self.status()

    def pause(self) -> dict:
        with self._lock:
            self._enabled = False
            self._paper_exploration_override = False
            self._last_signal = (
                "ENTRIES_PAUSED_STILL_MONITORING_POSITION"
                if self.ledger.open_position() else "PAUSED")
            return self.status()

    def _enter(self, quote: UpQuote, side: str, close: int, now: int, stats: dict) -> None:
        price = quote.ask + SLIPPAGE
        # The actual simulated entry must also remain inside the user cap.
        if price > D("0.85") or quote.bid_depth <= 0:
            self._last_signal = "NO_SAFE_SIMULATED_ENTRY"
            return
        budget = min(self._budget, stats["cash"])
        upper = min(quote.ask_depth, quote.bid_depth, budget / price, D("1000"))
        maximum = int((upper / D("0.01")).to_integral_value(rounding=ROUND_FLOOR))
        lo, hi = 0, max(0, maximum)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            qty = D(mid) / 100
            if qty * price + standard_taker_fee_estimate(price, qty) <= budget:
                lo = mid
            else:
                hi = mid - 1
        if lo <= 0:
            self._last_signal = "BUDGET_OR_DEPTH_TOO_SMALL"
            return
        qty = D(lo) / 100
        fee = standard_taker_fee_estimate(price, qty)
        self.ledger.insert(
            ticker=quote.ticker, side=side, quantity=qty,
            price=price, fee=fee, paid=qty * price + fee,
            now_ns=now, close_ns=close,
        )
        self._last_signal = f"PAPER_BOUGHT_{side}_FAST_MOMENTUM"

    def _exit(self, row: dict, quote: UpQuote, now: int) -> None:
        qty = D(row["quantity"])
        if quote.bid_depth < qty or quote.bid <= SLIPPAGE:
            self._last_signal = "EXIT_SIGNAL_LATCHED_WAITING_FOR_BID_DEPTH"
            return
        sale = quote.bid - SLIPPAGE
        fee = standard_taker_fee_estimate(sale, qty)
        pnl = qty * sale - fee - D(row["total_paid"])
        self.ledger.close(
            row["id"], price=sale, fee=fee, now_ns=now,
            reason=f"{row['side']}_MOMENTUM_SLOWED", pnl=pnl,
        )
        self._last_exit_ns = now
        self._exit_latched = False
        self._last_signal = f"PAPER_SOLD_{row['side']}_MOMENTUM_SLOWED"

    def tick(self, now_ns: int | None = None) -> dict:
        with self._lock:
            now = self.clock() if now_ns is None else now_ns
            stats = self._stats(now)
            opened = stats["open"]
            market_info = self._market(now)
            if opened is not None:
                ticker, close = opened["ticker"], opened["close_ns"]
            elif market_info is not None:
                ticker, close = market_info
            else:
                for history in self._samples.values():
                    history.clear()
                self._ticker = None
                self._last_signal = "WAITING_FOR_FRESH_MARKET"
                return self.status()

            if self._ticker != ticker:
                self._evidence_market = None
                self._entry_evidence = None
                for history in self._samples.values():
                    history.clear()
                self._last_ask_cents = None
                self._last_down_ask_cents = None
                self._breakeven_cents = {"UP": None, "DOWN": None}
                self._side_speeds = {"UP": 0.0, "DOWN": 0.0}
                self._side_reasons = {"UP": "WARMING_UP", "DOWN": "WARMING_UP"}
                self._ticker = ticker
                # Do not discard a latched liquidation on a market switch if
                # an existing inventory is still open in its old market.
                if opened is None:
                    self._exit_latched = False

            if close <= now:
                if opened is not None and not self._settle(opened, now):
                    self._last_signal = "WAITING_FOR_OFFICIAL_SETTLEMENT"
                return self.status()
            if opened is None and not self._enabled:
                self._last_signal = (
                    "QUOTE_TAPE_WRITE_FAILED" if self._capture_failed
                    else "DISARMED_OR_PAUSED"
                )
                return self.status()
            try:
                quotes = self._quote(ticker, now)
            except Exception as exc:
                self._last_signal = "QUOTE_UNAVAILABLE_" + type(exc).__name__
                return self.status()

            observed_ns = quotes["UP"].observed_ns
            if observed_ns >= close:
                self._last_signal = "MARKET_CLOSED_DURING_QUOTE"
                return self.status()
            for side in ("UP", "DOWN"):
                self._samples[side].append(quotes[side])
            self._last_ask_cents = format(quotes["UP"].ask * 100, ".2f")
            self._last_down_ask_cents = format(quotes["DOWN"].ask * 100, ".2f")
            for side, q in quotes.items():
                buy = q.ask + SLIPPAGE
                sell = q.bid - SLIPPAGE
                if D("0") < sell < buy < D("1"):
                    # One-contract illustrative all-in quote cost, not a
                    # forecast of the next fill or a profit guarantee.
                    drag = (buy - sell
                            + standard_taker_fee_estimate(buy, D("1"))
                            + standard_taker_fee_estimate(sell, D("1")))
                    self._breakeven_cents[side] = format(drag * 100, ".2f")
                else:
                    self._breakeven_cents[side] = None
            remain = (close - observed_ns) / NS
            # Use only earlier, fully closed market quote evidence. The
            # current market can never train its own entry veto.
            if self._evidence_market != ticker:
                try:
                    from .strat_evidence import evaluate_historical_entry_evidence
                    self._entry_evidence = evaluate_historical_entry_evidence(
                        self.tape.path, observed_ns,
                    )
                except Exception:
                    LOG.exception("Strat historical evidence unavailable")
                    self._entry_evidence = None
                self._evidence_market = ticker
            signals = {
                side: evaluate_side_momentum(
                    list(self._samples[side]), remain, side=side,
                    holding=opened is not None and opened["side"] == side,
                    exit_latched=self._exit_latched if opened is not None
                    and opened["side"] == side else False,
                )
                for side in ("UP", "DOWN")
            }
            # Persist contemporaneous UP+DOWN quotes and causal decisions.
            # If writing fails, do not create a new hypothetical position:
            # otherwise there is no timestamp-correct evidence to audit it.
            try:
                self.tape.append(
                    observed_ns=observed_ns, ticker=ticker, close_ns=close,
                    remaining_seconds=remain,
                    quotes=quotes,
                    signals={side: signals[side].reason for side in ("UP", "DOWN")},
                    position_side=opened["side"] if opened is not None else None,
                )
            except Exception:
                LOG.exception("Strat quote evidence not durable")
                if opened is None:
                    self._last_signal = "QUOTE_TAPE_WRITE_FAILED"
                    self._capture_failed = True
                    self._enabled = False
                    return self.status()

            for side in ("UP", "DOWN"):
                self._side_speeds[side] = signals[side].speed_cents_per_second
                self._side_reasons[side] = signals[side].reason
            leader = max(("UP", "DOWN"), key=lambda s: self._side_speeds[s])
            if opened is not None:
                leader = opened["side"]
            self._last_speed = self._side_speeds[leader]
            self._last_ratio = signals[leader].acceleration_ratio

            if opened is not None:
                held = opened["side"]
                if held not in ("UP", "DOWN"):
                    self._last_signal = "INVALID_OPEN_POSITION_SIDE"
                elif signals[held].action == "SELL" or self._exit_latched:
                    self._exit_latched = True
                    self._exit(opened, quotes[held], observed_ns)
                else:
                    self._last_signal = signals[held].reason
            elif self._market(observed_ns) != (ticker, close):
                self._last_signal = "MARKET_IDENTITY_NOT_FRESH"
            elif self.ledger.ticker_count(ticker) >= MAX_CYCLES:
                self._last_signal = "MAX_MARKET_CYCLES"
            elif now - self._last_exit_ns < COOLDOWN_NS:
                self._last_signal = "REENTRY_COOLDOWN"
            else:
                candidates = [
                    side for side in ("UP", "DOWN") if signals[side].action == "BUY"
                ]
                if len(candidates) == 1:
                    selected = candidates[0]
                    evidence = (self._entry_evidence or {}).get("sides", {}).get(
                        selected, {}
                    )
                    if (evidence.get("entry_veto") is True
                            and not self._paper_exploration_override):
                        self._last_signal = (
                            f"NEGATIVE_HISTORICAL_{selected}_TAKER_MARKOUT"
                        )
                    else:
                        self._enter(
                            quotes[selected], selected, close, observed_ns, stats
                        )
                elif len(candidates) > 1:
                    self._last_signal = "AMBIGUOUS_BIDIRECTIONAL_ACCELERATION"
                else:
                    self._last_signal = (
                        "UP:" + signals["UP"].reason
                        + " | DOWN:" + signals["DOWN"].reason
                    )
            return self.status()

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                LOG.exception("Strat paper tick failed closed")
            stop.wait(0.75)

    def close(self) -> None:
        self.client.close()
