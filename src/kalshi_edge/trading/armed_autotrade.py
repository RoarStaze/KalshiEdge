"""Explicitly armed, one-shot live Kalshi BUY after a verified final forecast.

No strategy runs until a same-origin user action arms a dollar amount.
This worker is deliberately non-persistent: restarts disarm it; no retries of
ambiguous exchange results, and no attempt is made after a missed checkpoint.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
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


class AutoTradeError(ValueError):
    pass


class ArmedAutoTrader:
    """One-at-a-time, ticker-scoped live order intent, not a recurring bot."""

    def __init__(self, manual, read_live: Callable[[], dict], *,
                 now_ns: Callable[[], int] = time.time_ns):
        self.manual = manual
        self.read_live = read_live
        self.now_ns = now_ns
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: dict[str, Any] = {"state": "IDLE", "ticker": None}
        # No second attempt for a market after ANY exchange order attempt,
        # even if the remote response is lost or the fill is partial.
        self._used: set[str] = set()

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def arm(self, amount: str, *, start_worker: bool = True) -> dict:
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
            if ticker in self._used:
                raise AutoTradeError("AutoTrade already used on this market")
        if type(close) is not int or type(checkpoint) is not int or close - checkpoint != FINAL_CHECKPOINT_SECONDS * NS:
            raise AutoTradeError("Current market checkpoint is unavailable")
        if live.get("final_forecast") is not None or now >= checkpoint:
            raise AutoTradeError("Final forecast already locked or checkpoint passed; arm next round")
        if close <= now:
            raise AutoTradeError("Market closed")
        with self._lock:
            if self._state["state"] in ("WAITING", "EXECUTING"):
                raise AutoTradeError("AutoTrade already armed")
            if ticker in self._used:
                raise AutoTradeError("AutoTrade already used on this market")
            self._state = {
                "state": "WAITING", "ticker": ticker,
                "amount": format(d, ".2f"), "checkpoint_ts_ns": checkpoint,
                "market_close_ts_ns": close, "armed_at_ns": now,
                "outcome": None, "order_id": None,
                "message": "Armed — waiting for locked final forecast",
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
            self._state.update(state="CANCELLED", message="AutoTrade cancelled")
            return dict(self._state)

    def _finish(self, state: str, message: str, **details) -> None:
        with self._lock:
            self._state.update(state=state, message=message, **details)

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
        if now >= close - MIN_EXECUTION_REMAINING_NS:
            self._finish("EXPIRED", "Too close to market close; no order submitted")
            return
        live = self.read_live()
        observed = live.get("market_ticker")
        if observed is not None and observed != ticker:
            self._finish("EXPIRED", "BTC market changed; no order submitted")
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
            # This is a real-money order, explicitly authorized by the Arm click.
            # Mark attempted BEFORE calling the exchange; no automatic retry.
            with self._lock:
                self._used.add(ticker)
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
                self._state.update(state="CANCELLED", message="Server stopped; disarmed")
