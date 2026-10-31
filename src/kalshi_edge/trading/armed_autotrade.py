"""Opt-in BTC AutoTrade, with an optional continuously armed session.

Each new market requires its own verified final forecast. Only the explicit
Arm action permits trading. Restarts disarm; no ambiguous exchange retry.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import logging
from .autotrade_history import AutoTradeHistory
import re
import threading
import time
from typing import Callable, Any

from .manual_trade import MAX_REQUEST_DOLLARS


NS = 1_000_000_000
FINAL_CHECKPOINT_SECONDS = 240
MAX_LOCK_DELAY_NS = 30 * NS
MIN_EXECUTION_REMAINING_NS = 40 * NS
TICKER_PATTERN = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
LOG = logging.getLogger(__name__)


class AutoTradeError(ValueError):
    pass


class ArmedAutoTrader:
    """One signed order attempt per ticker; optionally monitor future markets."""

    def __init__(self, manual, read_live: Callable[[], dict], *,
                 now_ns: Callable[[], int] = time.time_ns,
                 history_path: Path | None = None):
        self.manual = manual
        self.read_live = read_live
        self.now_ns = now_ns
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: dict[str, Any] = {"state": "IDLE", "ticker": None,
                                      "continuous": False}
        # No second attempt for a market after ANY exchange order attempt,
        # even if the remote response is lost or the fill is partial.
        self._used: set[str] = set()
        self._processed: set[str] = set()
        self.history_store = AutoTradeHistory(history_path) if history_path else None

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def history(self) -> dict:
        return {"entries": self.history_store.recent() if self.history_store else []}

    def _attempted(self, ticker: str) -> bool:
        return ticker in self._used or bool(
            self.history_store and self.history_store.has_attempt(ticker)
        )

    def arm(self, amount: str, *, continuous: bool = False,
            start_worker: bool = True) -> dict:
        if type(continuous) is not bool:
            raise AutoTradeError("Continuous mode must be boolean")
        try:
            d = Decimal(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AutoTradeError("Enter a valid dollar amount") from exc
        if not d.is_finite() or not Decimal("0.01") <= d <= MAX_REQUEST_DOLLARS:
            raise AutoTradeError("Enter a dollar amount between $0.01 and $100,000")
        if d.as_tuple().exponent < -2:
            raise AutoTradeError("Dollar amount supports at most two decimal places")
        live = self.read_live()
        ticker = live.get("market_ticker")
        close = live.get("market_close_ts_ns")
        checkpoint = live.get("checkpoint_ts_ns")
        now = self.now_ns()
        if not isinstance(ticker, str) or not TICKER_PATTERN.fullmatch(ticker):
            raise AutoTradeError("No readable current BTC 15-minute market")
        if self._attempted(ticker) and not continuous:
            raise AutoTradeError("AutoTrade already used on this market")
        if type(close) is not int or type(checkpoint) is not int or close - checkpoint != FINAL_CHECKPOINT_SECONDS * NS:
            raise AutoTradeError("Current market checkpoint is unavailable")
        already_past = live.get("final_forecast") is not None or now >= checkpoint
        if already_past and not continuous:
            raise AutoTradeError("Final forecast already locked or checkpoint passed; arm next round")
        if close <= now and not continuous:
            raise AutoTradeError("Market closed")
        with self._lock:
            if self._state["state"] in ("WAITING", "EXECUTING"):
                raise AutoTradeError("AutoTrade already armed")
            if self._stop.is_set():
                raise AutoTradeError("AutoTrade engine is stopped")
            if self._attempted(ticker) and not continuous:
                raise AutoTradeError("AutoTrade already used on this market")
            # Joining after this market has locked must never backfill
            # its trade. The arm remains active for the NEXT round.
            skip_current = already_past or self._attempted(ticker)
            if skip_current and continuous:
                self._processed.add(ticker)
            self._state = {
                "state": "WAITING", "ticker": ticker,
                "continuous": continuous, "amount": format(d, ".2f"),
                "checkpoint_ts_ns": checkpoint, "market_close_ts_ns": close,
                "armed_at_ns": now, "last_result": None,
                "outcome": None, "order_id": None,
                "message": ("Armed — waiting for next BTC market"
                            if skip_current and continuous
                            else "Armed — waiting for locked final forecast"),
            }
            result = dict(self._state)
        if start_worker:
            threading.Thread(target=self._loop, name="kalshiedge-armed-auto",
                             daemon=True).start()
        return result

    def cancel(self) -> dict:
        with self._lock:
            if self._state["state"] != "WAITING":
                raise AutoTradeError("Only an armed, waiting trade can be cancelled")
            self._state.update(state="CANCELLED", continuous=False,
                               message="AutoTrade cancelled; no further markets will trade")
            return dict(self._state)

    def _finish(self, state: str, message: str, **details) -> None:
        with self._lock:
            ticker = self._state.get("ticker")
            continue_session = (self._state.get("continuous") and state in (
                "FILLED", "PARTIAL", "NO_FILL", "SKIPPED", "EXPIRED", "NO_QUOTE"
            ))
            if continue_session:
                self._processed.add(ticker)
                self._state.update(state="WAITING", last_result=state,
                                   message=message+" · waiting for next market", **details)
            else:
                self._state.update(state=state, last_result=state,
                                   message=message, **details)
            snapshot = dict(self._state)
        if state not in ("FILLED", "PARTIAL", "NO_FILL", "UNKNOWN"):
            return
        if self.history_store is None or snapshot.get("outcome") not in ("UP", "DOWN"):
            return
        try:
            result = details.get("result") or {}
            self.history_store.record_result(
                ticker=snapshot["ticker"], status=state,
                order_id=result.get("order_id") or details.get("order_id"),
                fill_count=result.get("fill_count"),
            )
        except Exception:
            # The attempt was already committed before the exchange order.
            # A failed status update leaves UNKNOWN in the history view.
            LOG.exception("AutoTrade history status update failed; verify exchange orders")

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self._finish("ERROR", "Automation error before order; inspect logs")
                return
            if self.status()["state"] != "WAITING":
                return
            self._stop.wait(0.6)

    def tick(self) -> None:
        with self._lock:
            if self._state["state"] != "WAITING":
                return
            armed = dict(self._state)
        now = self.now_ns()
        ticker = armed["ticker"]
        close = armed["market_close_ts_ns"]
        checkpoint = armed["checkpoint_ts_ns"]
        live = self.read_live()
        observed = live.get("market_ticker")
        new_close = live.get("market_close_ts_ns")
        new_checkpoint = live.get("checkpoint_ts_ns")
        # An empty/stale live snapshot must never trigger an order or disarm
        # a continuous session that is still waiting for the next market.
        valid = (
            isinstance(observed, str) and TICKER_PATTERN.fullmatch(observed)
            and type(new_close) is int and type(new_checkpoint) is int
            and new_close - new_checkpoint == FINAL_CHECKPOINT_SECONDS * NS
        )
        if not valid:
            return
        if observed != ticker:
            if not armed.get("continuous"):
                self._finish("EXPIRED", "BTC market changed; no order submitted")
                return
            if new_close <= close:
                return  # old cached feed; no time travel
            with self._lock:
                if self._state["state"] != "WAITING" or self._state["ticker"] != ticker:
                    return
                # Arriving after final lock cannot retroactively authorize BUY.
                missed = (now >= new_checkpoint or
                          live.get("final_forecast") is not None or
                          self._attempted(observed))
                if missed:
                    self._processed.add(observed)
                self._state.update(
                    ticker=observed,
                    market_close_ts_ns=new_close,
                    checkpoint_ts_ns=new_checkpoint,
                    outcome=None, order_id=None,
                    message=("Waiting for the following BTC market"
                             if missed else
                             "New market found — waiting for locked final forecast"),
                )
                armed = dict(self._state)
            ticker, close, checkpoint = observed, new_close, new_checkpoint
        elif (new_close, new_checkpoint) != (close, checkpoint):
            return  # inconsistent snapshot for this ticker
        if ticker in self._processed or self._attempted(ticker):
            if not armed.get("continuous") and self._attempted(ticker):
                self._finish("EXPIRED", "AutoTrade already used for this market")
            return
        if now >= close - MIN_EXECUTION_REMAINING_NS:
            self._finish("EXPIRED", "Market closing; no order submitted")
            return
        if now < checkpoint:
            return
        if now > checkpoint + MAX_LOCK_DELAY_NS:
            self._finish("EXPIRED", "Final forecast arrived too late; no order submitted")
            return
        final = live.get("final_forecast")
        if not isinstance(final, dict):
            return
        if (final.get("market_ticker") != ticker
                or final.get("scheduled_ts_ns") != checkpoint):
            return
        direction = final.get("forecast")
        if direction not in ("ABOVE", "BELOW", "SKIP"):
            return
        confidence = final.get("confidence")
        if (direction == "SKIP"
                or not isinstance(confidence, (int, float))
                or not 0.69 <= confidence <= 1):
            self._finish("SKIPPED", "Final forecast SKIP / confidence under 69%; no order")
            return
        side = "UP" if direction == "ABOVE" else "DOWN"
        with self._lock:
            if self._state["state"] != "WAITING" or self._state["ticker"] != ticker:
                return
            self._state.update(state="EXECUTING", outcome=side,
                               confidence=float(confidence),
                               message="Locked forecast — preparing exchange order")
        try:
            preview = self.manual.preview(ticker, "BUY", side, "DOLLARS", armed["amount"])
            if (preview.get("ticker") != ticker or
                    preview.get("action") != "BUY" or preview.get("outcome") != side):
                self._finish("ERROR", "Quote does not match final forecast")
                return
            after = self.read_live()
            second_final = after.get("final_forecast") or {}
            if (after.get("market_ticker") != ticker
                    or after.get("market_close_ts_ns") != close
                    or after.get("checkpoint_ts_ns") != checkpoint
                    or second_final.get("market_ticker") != ticker
                    or second_final.get("forecast") != direction
                    or second_final.get("scheduled_ts_ns") != checkpoint
                    or self.now_ns() > checkpoint + MAX_LOCK_DELAY_NS
                    or self.now_ns() >= close - MIN_EXECUTION_REMAINING_NS):
                self._finish("EXPIRED", "Market or forecast changed before order")
                return
            # Record the market before POST; failure to persist blocks the
            # exchange operation. This also prevents replay after a restart.
            try:
                if self.history_store is not None:
                    self.history_store.record_attempt(
                        ticker=ticker, attempted_at_ns=self.now_ns(),
                        outcome=side, amount=armed["amount"],
                        confidence=float(confidence),
                    )
                with self._lock:
                    self._used.add(ticker)
            except Exception:
                LOG.exception("Could not persist AutoTrade attempt")
                self._finish("ERROR", "History save failed; no order submitted")
                return
            try:
                result = self.manual.place(preview["preview_id"], ticker)
            except Exception:
                self._finish("UNKNOWN", "Order result uncertain; inspect Kalshi before rearming")
                return
            status = result.get("status")
            if status not in ("FILLED", "PARTIAL", "NO_FILL"):
                self._finish("UNKNOWN", "Unexpected exchange order result; inspect Kalshi",
                             result=result)
                return
            self._finish(status, "Exchange confirmed "+status.replace("_", " ").lower(),
                         order_id=result.get("order_id"), result=result)
        except Exception:
            # No exchange POST if preview fails. The user explicitly requested
            # that ordinary no-liquidity rounds not cancel continuous arming.
            if armed.get("continuous"):
                self._finish("NO_QUOTE", "Could not obtain executable quote; no order")
            else:
                self._finish("ERROR", "Could not obtain executable quote; no order")

    def close(self):
        self._stop.set()
        with self._lock:
            if self._state["state"] == "WAITING":
                self._state.update(state="CANCELLED", message="Server stopped; disarmed")
