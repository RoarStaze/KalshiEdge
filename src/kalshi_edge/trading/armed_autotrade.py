"""Explicitly armed AutoTrade, optionally following successive BTC markets.

No strategy runs until a same-origin user action arms a dollar amount.
The arm session is deliberately non-persistent: restarts disarm it. Each market
has at most one order attempt; ambiguous results never retry.
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
    """One market order at a time, with opt-in continuous final-forecast mode."""

    def __init__(self, manual, read_live: Callable[[], dict], *,
                 now_ns: Callable[[], int] = time.time_ns,
                 history_path: Path | None = None):
        self.manual = manual
        self.read_live = read_live
        self.now_ns = now_ns
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: dict[str, Any] = {"state": "IDLE", "ticker": None}
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

    def arm(self, amount: str, *, continuous: bool = False,
            start_worker: bool = True) -> dict:
        if type(continuous) is not bool:
            raise AutoTradeError("Continuous mode must be a boolean")
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
        with self._lock:
            if ticker in self._used or (self.history_store and self.history_store.has_attempt(ticker)):
                raise AutoTradeError("AutoTrade already used on this market")
        if type(close) is not int or type(checkpoint) is not int or close - checkpoint != FINAL_CHECKPOINT_SECONDS * NS:
            raise AutoTradeError("Current market checkpoint is unavailable")
        if not continuous and (live.get("final_forecast") is not None or now >= checkpoint):
            raise AutoTradeError("Final forecast already locked or checkpoint passed; arm next round")
        if close <= now and not continuous:
            raise AutoTradeError("Market closed")
        with self._lock:
            if self._state["state"] in ("WAITING", "EXECUTING"):
                raise AutoTradeError("AutoTrade already armed")
            if ticker in self._used or (self.history_store and self.history_store.has_attempt(ticker)):
                raise AutoTradeError("AutoTrade already used on this market")
            # If the lock has passed, continuous mode must wait for a newly
            # observed market, never replay a final forecast from the past.
            skip_current = (continuous and
                            (now >= checkpoint or live.get("final_forecast") is not None))
            if skip_current:
                self._processed.add(ticker)
            self._state = {
                "state": "WAITING", "ticker": ticker,
                "continuous": continuous,
                "amount": format(d, ".2f"), "checkpoint_ts_ns": checkpoint,
                "market_close_ts_ns": close, "armed_at_ns": now,
                "outcome": None, "order_id": None, "last_result": None,
                "message": ("Armed — waiting for the next market" if skip_current
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
                               message="AutoTrade cancelled; no further markets")
            return dict(self._state)

    def _finish(self, state: str, message: str, **details) -> None:
        with self._lock:
            if self._state.get("continuous") and state in (
                "FILLED", "PARTIAL", "NO_FILL", "SKIPPED", "EXPIRED"
            ):
                self._processed.add(self._state["ticker"])
                self._state.update(state="WAITING", last_result=state,
                                   message=message+" · Waiting for next market", **details)
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
        ticker,close,checkpoint = (armed["ticker"],armed["market_close_ts_ns"],
                                   armed["checkpoint_ts_ns"])
        live = self.read_live()
        observed = live.get("market_ticker")
        if observed is not None and observed != ticker:
            if not armed.get("continuous"):
                self._finish("EXPIRED", "BTC market changed; no order submitted")
                return
            # Transition only to an independently verified *newer* market.
            next_close = live.get("market_close_ts_ns")
            next_checkpoint = live.get("checkpoint_ts_ns")
            if (not isinstance(observed,str) or not TICKER_PATTERN.fullmatch(observed)
                    or type(next_close) is not int or type(next_checkpoint) is not int
                    or next_close-next_checkpoint != FINAL_CHECKPOINT_SECONDS*NS
                    or next_close <= close):
                return
            with self._lock:
                if self._state["state"] != "WAITING" or self._state["ticker"] != ticker:
                    return
                already = (observed in self._processed or observed in self._used
                           or bool(self.history_store and self.history_store.has_attempt(observed)))
                late = now >= next_checkpoint or live.get("final_forecast") is not None
                if already or late:
                    self._processed.add(observed)
                self._state.update(
                    ticker=observed, market_close_ts_ns=next_close,
                    checkpoint_ts_ns=next_checkpoint,
                    outcome=None, order_id=None,
                    message=("Waiting for next market" if already or late
                             else "New market detected — waiting for final forecast"),
                )
                armed=dict(self._state)
            ticker,close,checkpoint=observed,next_close,next_checkpoint
        if live.get("market_ticker") != ticker or live.get("market_close_ts_ns") != close:
            return
        with self._lock:
            if ticker in self._processed:
                return
        if now >= close - MIN_EXECUTION_REMAINING_NS:
            self._finish("EXPIRED", "Too close to market close; no order submitted")
            return
        if now < checkpoint:
            return
        if now > checkpoint + MAX_LOCK_DELAY_NS:
            self._finish("EXPIRED", "Final forecast was not received promptly; no order submitted")
            return
        final = live.get("final_forecast")
        if not isinstance(final, dict):
            return
        # Never use a live preview or a final decision for a different market.
        if (final.get("market_ticker") != ticker
                or final.get("scheduled_ts_ns") != checkpoint
                or live.get("market_close_ts_ns") != close):
            return
        direction = final.get("forecast")
        if direction not in ("ABOVE", "BELOW", "SKIP"):
            return
        confidence = final.get("confidence")
        if direction == "SKIP" or not isinstance(confidence, (int, float)) or not 0.69 <= confidence <= 1:
            self._finish("SKIPPED", "Final forecast SKIP or confidence below 69%; no order")
            return
        side = "UP" if direction == "ABOVE" else "DOWN"
        with self._lock:
            if self._state["state"] != "WAITING" or self._state["ticker"] != ticker:
                return
            self._state.update(state="EXECUTING", outcome=side,
                               confidence=float(confidence),
                               message="Locked forecast received — checking live quote")
        try:
            preview = self.manual.preview(ticker, "BUY", side, "DOLLARS", armed["amount"])
            if (preview.get("ticker") != ticker or preview.get("action") != "BUY"
                    or preview.get("outcome") != side):
                self._finish("ERROR", "Exchange quote does not match locked decision")
                return
            # Recheck immediately before POST; never trade on a rolled market.
            after = self.read_live()
            second_final = after.get("final_forecast") or {}
            if (after.get("market_ticker") != ticker
                    or after.get("market_close_ts_ns") != close
                    or second_final.get("market_ticker") != ticker
                    or second_final.get("forecast") != direction
                    or second_final.get("scheduled_ts_ns") != checkpoint
                    or self.now_ns() > checkpoint + MAX_LOCK_DELAY_NS
                    or self.now_ns() >= close - MIN_EXECUTION_REMAINING_NS):
                self._finish("EXPIRED", "Market or forecast changed before order; no trade")
                return
            # Commit an execution attempt BEFORE contacting the exchange.
            # If recording fails, no order may be submitted. A crashed process
            # leaves an UNKNOWN record rather than assuming a fill.
            with self._lock:
                self._used.add(ticker)
            try:
                if self.history_store is not None:
                    self.history_store.record_attempt(
                        ticker=ticker, attempted_at_ns=self.now_ns(),
                        outcome=side, amount=armed["amount"],
                        confidence=float(confidence),
                    )
            except Exception:
                LOG.exception("AutoTrade attempt journal write failed")
                self._finish("ERROR", "Cannot save attempt history; order not submitted")
                return
            try:
                result = self.manual.place(preview["preview_id"], ticker)
            except Exception:
                self._finish("UNKNOWN", "Order outcome uncertain — inspect Kalshi orders; no retry")
                return
            self._finish(result.get("status", "UNKNOWN"),
                         "Exchange order submitted; verify fills on Kalshi",
                         order_id=result.get("order_id"), result=result)
        except Exception:
            # Quote/calculation/account error before any order POST.
            self._finish("ERROR", "Could not obtain executable quote; no order submitted")

    def close(self):
        self._stop.set()
        with self._lock:
            if self._state["state"] == "WAITING":
                self._state.update(state="CANCELLED", continuous=False,
                                   message="Server stopped; disarmed")
