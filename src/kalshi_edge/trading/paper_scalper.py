"""Independent paper-only BTC 15-minute intramarket scalper.

Reads only public order books and a saved local final forecast. Simulated
fills require visible top-level depth and are marked with pessimistic
per-contract slippage and fee reserves. No signed API client, keys, POSTs,
actual fills or real-money position bookkeeping exist in this module.

Stops are research exit signals, not guaranteed exchange execution.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
import logging
import sqlite3
import threading
import time
from typing import Callable

from .paper import PublicMarketClient
from .risk import taker_fee_reserve
from .scalp_policy import (
    ScalpPosition, ScalpQuote, entry_signal, exit_signal, quote_from_kalshi,
)

LOG = logging.getLogger(__name__)
D = Decimal
NS = 1_000_000_000
STEP = D("0.01")
SIMULATED_SLIPPAGE_PER_CONTRACT = D("0.01")
MAX_CONTRACTS = D("1000")
MAX_PAPER_BUDGET_FRACTION = D("0.05")
DAILY_PAPER_LOSS_LIMIT = D("2")
MAX_PAPER_CYCLES_PER_TICKER = 3
REENTRY_COOLDOWN_NS = 10 * NS
VALID_TICKER_PREFIX = "KXBTC15M-"
SIMULATION_ACTIONS = frozenset({
    "STOP_LOSS_SIGNAL", "TAKE_PROFIT_SIGNAL",
    "TRAILING_STOP_SIGNAL", "TIME_EXIT_SIGNAL",
})


def _number(value) -> D:
    if value is None or isinstance(value, bool):
        raise ValueError("Invalid paper amount")
    try:
        amount = D(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid paper amount") from exc
    if not amount.is_finite():
        raise ValueError("Nonfinite paper amount")
    return amount


def _money(amount: D) -> str:
    return format(amount.quantize(D("0.000001")), ".6f")


def _utc_day(ns: int):
    return datetime.fromtimestamp(ns / NS, tz=timezone.utc).date()


class PaperScalpLedger:
    """Durable, local, independently auditable hypothetical round trips."""

    def __init__(self, path: Path, capital: D):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('UP','DOWN')),
                status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED','SETTLED')),
                quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                entry_fee TEXT NOT NULL,
                total_paid TEXT NOT NULL,
                entered_at_ns INTEGER NOT NULL,
                close_ns INTEGER NOT NULL,
                peak_net TEXT NOT NULL DEFAULT '0',
                exit_price TEXT,
                exit_fee TEXT,
                exit_ns INTEGER,
                exit_reason TEXT,
                paper_net_pnl TEXT
            )""")
            # Prevent two simultaneous hypothetical inventories even if a
            # second process accidentally shares the same SQLite file.
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_open_paper_scalp "
                       "ON cycles(status) WHERE status='OPEN'")
            db.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES('initial_bankroll',?)",
                (_money(capital),),
            )
            initial = db.execute(
                "SELECT value FROM settings WHERE key='initial_bankroll'"
            ).fetchone()
            self.initial_bankroll = _number(initial["value"])

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recent(self, limit: int = 30) -> list[dict]:
        with self.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()]

    def open_position(self) -> dict | None:
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM cycles WHERE status='OPEN' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None

    def ticker_count(self, ticker: str) -> int:
        with self.db() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM cycles WHERE ticker=?", (ticker,)
            ).fetchone()[0])

    def insert(self, *, ticker: str, side: str, quantity: D,
               price: D, fee: D, paid: D, now_ns: int, close_ns: int) -> None:
        with self.db() as db:
            db.execute(
                """INSERT INTO cycles (
                    ticker,side,status,quantity,entry_price,entry_fee,total_paid,
                    entered_at_ns,close_ns,peak_net)
                    VALUES (?,?,'OPEN',?,?,?,?,?,?,'0')""",
                (ticker, side, _money(quantity), _money(price), _money(fee),
                 _money(paid), now_ns, close_ns),
            )

    def set_peak(self, position_id: int, peak: D) -> None:
        with self.db() as db:
            db.execute(
                "UPDATE cycles SET peak_net=? WHERE id=? AND status='OPEN'",
                (_money(peak), position_id),
            )

    def close(self, position_id: int, *, price: D, fee: D,
              now_ns: int, reason: str, pnl: D, settled: bool = False) -> None:
        state = "SETTLED" if settled else "CLOSED"
        with self.db() as db:
            result = db.execute(
                """UPDATE cycles SET status=?, exit_price=?, exit_fee=?,
                   exit_ns=?, exit_reason=?, paper_net_pnl=?
                   WHERE id=? AND status='OPEN'""",
                (state, _money(price), _money(fee), now_ns, reason,
                 _money(pnl), position_id),
            )
            if result.rowcount != 1:
                raise RuntimeError("Paper position changed; refuse duplicate close")


class PaperScalpTrader:
    """Opt-in read-only paper worker; always disabled for NEW entries on restart.

    While an OPEN paper cycle exists, pausing the engine does not abandon
    observation or delete it. No real order can be constructed by this class.
    """

    def __init__(self, root: Path, read_live: Callable[[], dict], *,
                 client=None, now_ns: Callable[[], int] | None = None,
                 initial_bankroll: D = D("100"),
                 settlement_reader: Callable[[str], str | None] | None = None):
        if not isinstance(initial_bankroll, D) or (
                not initial_bankroll.is_finite()) or initial_bankroll <= 0:
            raise ValueError("Invalid simulated starting capital")
        self.ledger = PaperScalpLedger(
            Path(root).resolve() / "trading" / "paper_scalp.sqlite3",
            initial_bankroll,
        )
        self.read_live = read_live
        self.client = client if client is not None else PublicMarketClient()
        self.now_ns = now_ns or time.time_ns
        self.settlement_reader = settlement_reader
        self._lock = threading.RLock()
        self._enabled = False
        self._amount = D("1")
        self._last_signal = (
            "RECOVERED_OPEN_PAPER_POSITION" if self.ledger.open_position()
            else "IDLE"
        )
        self._history: deque[ScalpQuote] = deque(maxlen=3)
        self._history_ticker: str | None = None

    def _risk(self, now_ns: int):
        rows = self.ledger.recent(100000)
        closed = [r for r in rows if r["status"] in ("CLOSED", "SETTLED")]
        net = sum((_number(r["paper_net_pnl"]) for r in closed), D("0"))
        capital = self.ledger.initial_bankroll + net
        opened = next((r for r in rows if r["status"] == "OPEN"), None)
        paid = _number(opened["total_paid"]) if opened else D("0")
        daily_losses = sum((
            -_number(r["paper_net_pnl"]) for r in closed
            if _utc_day(r["exit_ns"]) == _utc_day(now_ns)
            and _number(r["paper_net_pnl"]) < 0
        ), D("0"))
        losing_streak = 0
        for row in closed:
            if _number(row["paper_net_pnl"]) >= 0:
                break
            losing_streak += 1
        return {
            "capital": capital,
            "cash": capital - paid,
            "simulated_pnl": net,
            "daily_losses": daily_losses,
            "loss_streak": losing_streak,
            "open": opened,
        }

    def status(self) -> dict:
        with self._lock:
            now = self.now_ns()
            risk = self._risk(now)
            opened = dict(risk["open"]) if risk["open"] else None
            if opened is not None:
                # An open position has no realized P/L.
                opened["paper_net_pnl"] = None
            return {
                "mode": "PAPER_SCALP_SIMULATED",
                "enabled": self._enabled,
                "live_order_submission": False,
                "requires_rearm_after_restart": True,
                "budget_dollars": _money(self._amount),
                "initial_bankroll_dollars": _money(self.ledger.initial_bankroll),
                "virtual_equity_excluding_open_mark_dollars": _money(risk["capital"]),
                "virtual_cash_dollars": _money(risk["cash"]),
                "simulated_net_pnl_dollars": _money(risk["simulated_pnl"]),
                "daily_simulated_losses_dollars": _money(risk["daily_losses"]),
                "consecutive_simulated_losses": risk["loss_streak"],
                "open_position": opened,
                "recent_cycles": self.ledger.recent(30),
                "last_signal": self._last_signal,
                "warning": (
                    "Hypothetical price/depth fills with 1-cent slippage on each side "
                    "and conservative fee reserves. No real orders or verified profit. "
                    "Stops may be blocked by missing liquidity."
                ),
            }

    def arm(self, amount: str) -> dict:
        with self._lock:
            value = _number(amount)
            if value.as_tuple().exponent < -2 or value < D("0.10"):
                raise ValueError("Paper budget requires at least $0.10, at most 2 decimals")
            now = self.now_ns()
            risk = self._risk(now)
            cap = min(
                self.ledger.initial_bankroll * MAX_PAPER_BUDGET_FRACTION,
                risk["cash"] * MAX_PAPER_BUDGET_FRACTION,
            )
            if value > cap or value > D("50"):
                raise ValueError("Paper budget exceeds 5% virtual cash or $50 maximum")
            self._amount = value
            self._enabled = True
            self._last_signal = "WAITING_FOR_FEE_ADJUSTED_ENTRY"
            return self.status()

    def pause(self) -> dict:
        with self._lock:
            self._enabled = False
            self._last_signal = (
                "PAPER_ENTRIES_PAUSED_POSITION_MONITORED"
                if self.ledger.open_position() else "PAPER_ENTRIES_PAUSED"
            )
            return self.status()

    @staticmethod
    def _is_market_open(payload: dict, ticker: str) -> bool:
        market = payload.get("market") if isinstance(payload, dict) else None
        return (isinstance(market, dict)
                and market.get("ticker") == ticker
                and market.get("status") in ("active", "open"))

    def _quote(self, ticker: str, side: str, now: int) -> ScalpQuote:
        market, book = self.client.quote(ticker)
        if not self._is_market_open(market, ticker):
            raise ValueError("Public market not active or identity mismatch")
        return quote_from_kalshi(book, side, timestamp_ns=now)

    @staticmethod
    def _position(row: dict) -> ScalpPosition:
        return ScalpPosition(
            side=row["side"], quantity=_number(row["quantity"]),
            total_paid=_number(row["total_paid"]),
            entered_at_ns=row["entered_at_ns"],
            peak_net_dollars=_number(row["peak_net"]),
        )

    def _closing_payout(self, row: dict, now: int, actual_side: str) -> None:
        qty = _number(row["quantity"])
        own_result = "yes" if row["side"] == "UP" else "no"
        price = D("1") if actual_side == own_result else D("0")
        pnl = qty * price - _number(row["total_paid"])
        self.ledger.close(row["id"], price=price, fee=D("0"),
                          now_ns=now, reason="OFFICIAL_SETTLEMENT", pnl=pnl,
                          settled=True)
        self._last_signal = "SIMULATED_OFFICIAL_SETTLEMENT"
        self._history.clear()

    def _tick_open(self, row: dict, now: int, settlement) -> None:
        ticker = row["ticker"]
        if now >= row["close_ns"]:
            verified = settlement
            if verified is None and self.settlement_reader is not None:
                try:
                    actual = self.settlement_reader(ticker)
                    verified = (ticker, actual) if actual is not None else None
                except Exception:
                    LOG.exception("Cannot read verified paper settlement")
            if (isinstance(verified, tuple) and len(verified) == 2
                    and verified[0] == ticker and verified[1] in ("yes", "no")):
                self._closing_payout(row, now, verified[1])
            else:
                self._last_signal = "AWAITING_OFFICIAL_SETTLEMENT"
            return
        try:
            quote = self._quote(ticker, row["side"], now)
        except Exception:
            self._last_signal = "EXIT_BLOCKED_PUBLIC_QUOTE"
            return
        # Apply pessimistic simulated slippage before stop/profit tests.
        executable = quote.bid - SIMULATED_SLIPPAGE_PER_CONTRACT
        if executable <= 0:
            self._last_signal = "EXIT_BLOCKED_PRICE"
            return
        adjusted = replace(
            quote, bid=executable, spread=quote.ask-executable,
        )
        position = self._position(row)
        decision = exit_signal(
            position, adjusted, now_ns=now,
            seconds_remaining=max(0, int((row["close_ns"]-now) / NS)),
        )
        if decision.mark_net_dollars is not None:
            peak = max(position.peak_net_dollars, decision.mark_net_dollars)
            if peak != position.peak_net_dollars:
                self.ledger.set_peak(row["id"], peak)
        if decision.action not in SIMULATION_ACTIONS:
            self._last_signal = decision.action
            return
        if quote.bid_available < position.quantity:
            self._last_signal = "EXIT_BLOCKED_LIQUIDITY"
            return
        fee = taker_fee_reserve(executable, position.quantity)
        proceeds = position.quantity * executable - fee
        pnl = proceeds - position.total_paid
        self.ledger.close(
            row["id"], price=executable, fee=fee, now_ns=now,
            reason=decision.action, pnl=pnl,
        )
        self._history.clear()
        self._last_signal = "PAPER_" + decision.action

    def _live_entry_side(self, now: int):
        try:
            live = self.read_live()
        except Exception:
            return None
        if not isinstance(live, dict) or live.get("status") != "LIVE":
            return None
        ticker = live.get("market_ticker")
        close = live.get("market_close_ts_ns")
        checkpoint = live.get("checkpoint_ts_ns")
        final = live.get("final_forecast")
        if (not isinstance(ticker, str)
                or not ticker.startswith(VALID_TICKER_PREFIX)
                or not ticker.replace("-", "").isalnum()
                or type(close) is not int or type(checkpoint) is not int
                or close - checkpoint != 480 * NS
                or not checkpoint <= now < close
                or not isinstance(final, dict)
                or final.get("market_ticker") != ticker
                or final.get("scheduled_ts_ns") != checkpoint
                or final.get("forecast") not in ("ABOVE", "BELOW")):
            return None
        try:
            probability = _number(final.get("directional_probability"))
        except ValueError:
            return None
        if not D("0.69") < probability <= 1:
            return None
        side = "UP" if final["forecast"] == "ABOVE" else "DOWN"
        return ticker, side, close, probability

    @staticmethod
    def _size(budget: D, quote: ScalpQuote) -> tuple[D, D, D, D] | None:
        entry_price = quote.ask + SIMULATED_SLIPPAGE_PER_CONTRACT
        if entry_price >= 1:
            return None
        upper = min(
            MAX_CONTRACTS, quote.ask_available, quote.bid_available,
            budget / entry_price,
        )
        hi = int((upper / STEP).to_integral_value(rounding=ROUND_FLOOR))
        lo = 0
        while lo < hi:
            mid = (lo + hi + 1) // 2
            qty = mid * STEP
            cost = qty * entry_price + taker_fee_reserve(entry_price, qty)
            if cost <= budget:
                lo = mid
            else:
                hi = mid - 1
        if lo <= 0:
            return None
        qty = lo * STEP
        fee = taker_fee_reserve(entry_price, qty)
        paid = qty * entry_price + fee
        return qty, entry_price, fee, paid

    def _tick_flat(self, now: int) -> None:
        if not self._enabled:
            return
        risk = self._risk(now)
        if risk["daily_losses"] >= DAILY_PAPER_LOSS_LIMIT:
            self._last_signal = "DAILY_SIMULATED_LOSS_LIMIT"
            return
        if risk["loss_streak"] >= 3:
            self._last_signal = "THREE_SIMULATED_LOSSES"
            return
        info = self._live_entry_side(now)
        if info is None:
            self._history.clear()
            self._last_signal = "WAITING_FOR_VERIFIED_69_PERCENT_FINAL"
            return
        ticker, side, close, probability = info
        if self.ledger.ticker_count(ticker) >= MAX_PAPER_CYCLES_PER_TICKER:
            self._last_signal = "MARKET_CYCLE_LIMIT"
            return
        previous = self.ledger.recent(1)
        if previous and previous[0]["exit_ns"] is not None:
            if now - previous[0]["exit_ns"] < REENTRY_COOLDOWN_NS:
                self._last_signal = "REENTRY_COOLDOWN"
                return
        if self._history_ticker != ticker or (
                self._history and self._history[-1].side != side):
            self._history.clear()
            self._history_ticker = ticker
        try:
            quote = self._quote(ticker, side, now)
        except Exception:
            self._history.clear()
            self._last_signal = "NO_VALID_PUBLIC_SCALP_BOOK"
            return
        if self._history and quote.timestamp_ns <= self._history[-1].timestamp_ns:
            self._last_signal = "NONMONOTONIC_BOOK_OBSERVATION"
            return
        self._history.append(quote)
        budget = min(self._amount, risk["cash"] * MAX_PAPER_BUDGET_FRACTION)
        sized = self._size(budget, quote)
        if sized is None:
            self._last_signal = "NO_FEE_INCLUSIVE_PAPER_SIZE"
            return
        qty, entry_price, fee, paid = sized
        decision = entry_signal(
            tuple(self._history), probability, qty,
            int((close - now) / NS),
        )
        self._last_signal = decision.reason
        if decision.action != "RESEARCH_BUY_SIGNAL":
            return
        exit_bid = quote.bid - SIMULATED_SLIPPAGE_PER_CONTRACT
        if exit_bid <= 0:
            self._last_signal = "NO_EXECUTABLE_SIMULATED_EXIT"
            return
        exit_fee = taker_fee_reserve(exit_bid, qty)
        immediate_loss = paid - (qty * exit_bid - exit_fee)
        stop = max(D("0.02"), paid * D("0.08"))
        if immediate_loss >= stop:
            self._last_signal = "STOP_ALREADY_BREACHED_BY_SIMULATED_COSTS"
            return
        # Unique index and single serialized tick prevent double inventory.
        self.ledger.insert(
            ticker=ticker, side=side, quantity=qty, price=entry_price,
            fee=fee, paid=paid, now_ns=now, close_ns=close,
        )
        self._history.clear()
        self._last_signal = "PAPER_BUY_SIMULATED"

    def tick(self, now_ns: int | None = None, *, settlement=None) -> dict:
        """One bounded, serial, public-data-only paper observation."""
        with self._lock:
            now = self.now_ns() if now_ns is None else now_ns
            if type(now) is not int or now <= 0:
                raise ValueError("Invalid paper observation time")
            opened = self.ledger.open_position()
            if opened is not None:
                self._tick_open(opened, now, settlement)
            else:
                self._tick_flat(now)
            return self.status()

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                LOG.exception("Paper scalp observation failed; no orders were submitted")
            if stop.wait(3.0):
                return

    def close(self) -> None:
        # Only the separate public market client is owned by this instance.
        try:
            self.client.close()
        except Exception:
            LOG.warning("Paper public quote client cleanup failed", exc_info=True)
