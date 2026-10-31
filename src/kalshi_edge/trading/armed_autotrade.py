"""Explicit, market-scoped AutoTrade: early 90% entry, final fallback and reversal.

A user must arm the session. Every exchange POST is journaled BEFORE submission
with a durable unique (ticker,stage) key. Retrying is limited to a second IOC
only after a *confirmed zero fill*. Unknown outcomes stop the session.
Restarts disarm, and previously attempted markets cannot be replayed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
import math
from pathlib import Path
import re
import threading
import time
from typing import Callable

from .autotrade_history import AutoTradeHistory
from .autotrade_pnl import enrich_history
from .auto_edge import assess_auto_buy_edge
from .manual_trade import MAX_REQUEST_DOLLARS

LOG = logging.getLogger(__name__)
NS = 1_000_000_000
FINAL_CHECKPOINT_SECONDS = 240
MAX_LOCK_DELAY_NS = 30 * NS
EARLY_ENTRY_WINDOW_NS = 8 * 60 * NS
EARLY_CONFIDENCE_THRESHOLD = 0.90
MAX_EARLY_PREDICTION_AGE_NS = 8 * NS
MIN_EXECUTION_REMAINING_NS = 40 * NS
MAX_NO_FILL_ATTEMPTS = 2
RETRY_DELAY_NS = 2 * NS
TICKER_PATTERN = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")


class AutoTradeError(ValueError):
    pass


class ArmedAutoTrader:
    """Serial, explicitly armed AutoTrade worker. Only the current market trades."""

    def __init__(self, manual, read_live: Callable[[], dict], *,
                 now_ns: Callable[[], int] = time.time_ns,
                 history_path: Path | None = None):
        self.manual = manual
        self.read_live = read_live
        self.now_ns = now_ns
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._generation = 0
        self._busy = threading.Lock()
        self._state = {"state": "IDLE", "ticker": None, "continuous": False}
        self._used: set[str] = set()
        self._processed: set[str] = set()
        self.history_store = AutoTradeHistory(history_path) if history_path else None
        self._history_fetch_lock = threading.Lock()
        self._history_cache = None

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def history(self) -> dict:
        if self.history_store is None:
            return {"entries": []}
        rows = self.history_store.recent()
        for row in rows:
            row["stages"] = self.history_store.recent_stages(row["ticker"])
        # API history reads may be relatively expensive. Only reuse cached
        # reconciliation while the underlying journal remains unchanged.
        fingerprint = tuple(
            (row["ticker"], row["status"], row["fill_count"],
             tuple((stage["stage"], stage["status"], stage["order_id"],
                    stage["fill_count"]) for stage in row["stages"]))
            for row in rows
        )
        with self._history_fetch_lock:
            cached = self._history_cache
            now = time.monotonic()
            if (cached and cached["fingerprint"] == fingerprint
                    and now - cached["at"] < 30):
                return {"entries": [dict(entry) for entry in cached["entries"]]}
            client = getattr(self.manual, "client", None)
            enriched = enrich_history(rows, client)
            self._history_cache = {"fingerprint": fingerprint,
                                   "at": time.monotonic(), "entries": enriched}
            return {"entries": [dict(entry) for entry in enriched]}

    def _attempted(self, ticker: str) -> bool:
        return ticker in self._used or bool(
            self.history_store and self.history_store.has_attempt(ticker)
        )

    def _snapshot(self):
        try:
            live = self.read_live()
            return live if isinstance(live, dict) else {}
        except Exception as exc:
            LOG.debug("Waiting for live BTC market feed: %s", exc)
            return {}

    @staticmethod
    def _market(live: dict):
        ticker = live.get("market_ticker")
        close = live.get("market_close_ts_ns")
        checkpoint = live.get("checkpoint_ts_ns")
        if (not isinstance(ticker, str) or not TICKER_PATTERN.fullmatch(ticker)
                or type(close) is not int or type(checkpoint) is not int
                or close - checkpoint != FINAL_CHECKPOINT_SECONDS * NS):
            return None
        return ticker, close, checkpoint

    def arm(self, amount: str, *, continuous: bool = False,
            start_worker: bool = True) -> dict:
        if type(continuous) is not bool:
            raise AutoTradeError("Continuous mode must be boolean")
        try:
            value = Decimal(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AutoTradeError("Enter a valid dollar amount") from exc
        if not value.is_finite() or not Decimal("0.01") <= value <= MAX_REQUEST_DOLLARS:
            raise AutoTradeError("Enter a dollar amount between $0.01 and $100,000")
        if value.as_tuple().exponent < -2:
            raise AutoTradeError("Dollar amount supports at most two decimal places")
        live = self._snapshot()
        market = self._market(live)
        if market is None:
            raise AutoTradeError("No readable current BTC 15-minute market")
        ticker, close, checkpoint = market
        now = self.now_ns()
        already = self._attempted(ticker)
        if already and not continuous:
            raise AutoTradeError("AutoTrade already used on this market")
        if not continuous and (now >= checkpoint or live.get("final_forecast") is not None):
            raise AutoTradeError("Final forecast already locked or checkpoint passed; arm next round")
        if not continuous and close <= now:
            raise AutoTradeError("Market closed")
        with self._lock:
            if self._state["state"] in ("WAITING", "EXECUTING"):
                raise AutoTradeError("AutoTrade already armed")
            if self._stop.is_set():
                raise AutoTradeError("AutoTrade engine is stopped")
            skip = already or now >= checkpoint or live.get("final_forecast") is not None
            if skip:
                self._processed.add(ticker)
            self._state = {
                "state": "WAITING", "ticker": ticker, "continuous": continuous,
                "amount": format(value, ".2f"), "armed_at_ns": now,
                "market_close_ts_ns": close, "checkpoint_ts_ns": checkpoint,
                "outcome": None, "order_id": None, "trigger": None,
                "early_side": None, "early_filled": "0",
                "early_attempts": 0, "final_attempts": 0,
                "early_retry_after_ns": 0, "final_retry_after_ns": 0,
                "sold_for_reversal": False, "last_result": None,
                "message": ("Waiting for next BTC market" if skip
                            else "Armed — waiting for early signal or final forecast"),
            }
            self._generation += 1
            generation = self._generation
            state = dict(self._state)
        if start_worker:
            threading.Thread(target=self._loop, args=(generation,),
                             name="kalshiedge-armed-auto", daemon=True).start()
        return state

    def cancel(self) -> dict:
        with self._lock:
            if self._state["state"] != "WAITING":
                raise AutoTradeError("Only an armed, waiting trade can be cancelled")
            self._generation += 1
            self._state.update(state="CANCELLED", continuous=False,
                               message="AutoTrade cancelled; no further orders")
            return dict(self._state)

    def _set(self, **kwargs):
        with self._lock:
            self._state.update(**kwargs)

    def _finish(self, state: str, message: str, **details):
        with self._lock:
            if self._state.get("continuous") and state in (
                "FILLED", "PARTIAL", "NO_FILL", "SKIPPED", "EXPIRED", "NO_QUOTE"
            ):
                self._processed.add(self._state["ticker"])
                self._state.update(state="WAITING", last_result=state,
                                   message=message+" · waiting for next market", **details)
            else:
                self._state.update(state=state, last_result=state,
                                   message=message, **details)

    def _loop(self, generation: int):
        while not self._stop.is_set():
            with self._lock:
                if generation != self._generation or self._state["state"] != "WAITING":
                    return
            try:
                self.tick()
            except Exception:
                LOG.exception("AutoTrade engine stopped")
                with self._lock:
                    active = self._generation == generation
                if active:
                    self._finish("ERROR", "Unexpected AutoTrade error; review logs")
                return
            with self._lock:
                if generation != self._generation or self._state["state"] != "WAITING":
                    return
            self._stop.wait(0.6)

    def _early_signal(self, live: dict, *, now: int, ticker: str,
                      close: int, checkpoint: int):
        remain = close - now
        if (not 0 < remain <= EARLY_ENTRY_WINDOW_NS or now >= checkpoint
                or self._market(live) != (ticker, close, checkpoint)
                or live.get("final_forecast") is not None
                or live.get("status") != "LIVE"):
            return None
        pred = live.get("latest_prediction")
        if not isinstance(pred, dict):
            return None
        timestamp, confidence = pred.get("prediction_ts_ns"), pred.get("confidence")
        if (pred.get("market_ticker") != ticker or pred.get("status") != "OK"
                or pred.get("predicted_side") not in ("ABOVE", "BELOW")
                or type(timestamp) is not int
                or not 0 <= now - timestamp <= MAX_EARLY_PREDICTION_AGE_NS
                or type(confidence) not in (int,float)
                or not math.isfinite(confidence)
                or not EARLY_CONFIDENCE_THRESHOLD <= confidence <= 1):
            return None
        return pred["predicted_side"], float(confidence)

    @staticmethod
    def _locked(live: dict, ticker: str, checkpoint: int):
        final = live.get("final_forecast")
        if not isinstance(final, dict):
            return None
        if (final.get("market_ticker") != ticker
                or final.get("scheduled_ts_ns") != checkpoint
                or final.get("forecast") not in ("ABOVE", "BELOW", "SKIP")):
            return None
        return final

    def _matches_signal(self, ticker, close, checkpoint, direction, trigger):
        now = self.now_ns()
        live = self._snapshot()
        if self._market(live) != (ticker,close,checkpoint):
            return False
        if now >= close - MIN_EXECUTION_REMAINING_NS:
            return False
        if trigger == "EARLY":
            signal = self._early_signal(
                live, now=now, ticker=ticker, close=close, checkpoint=checkpoint)
            return signal is not None and signal[0] == direction
        final = self._locked(live, ticker, checkpoint)
        return (checkpoint <= now <= checkpoint+MAX_LOCK_DELAY_NS
                and final is not None and final.get("forecast") == direction)

    def _order(self, *, armed: dict, stage: str, action: str, side: str,
               amount: str, units: str, direction: str, trigger: str,
               confidence: float, sell_exact: Decimal | None = None):
        """Return (status, exchange data). NONE/NO_QUOTE means no order sent."""
        ticker=armed["ticker"]
        close=armed["market_close_ts_ns"]
        checkpoint=armed["checkpoint_ts_ns"]
        try:
            preview=self.manual.preview(ticker,action,side,units,amount)
        except Exception:
            return "NO_QUOTE", {}
        if (preview.get("ticker") != ticker or preview.get("action") != action
                or preview.get("outcome") != side
                or not isinstance(preview.get("preview_id"),str)):
            return "NO_QUOTE", {}
        if sell_exact is not None:
            try:
                if Decimal(preview["count"]) != sell_exact:
                    return "NO_QUOTE", {}
            except (KeyError, InvalidOperation, ValueError):
                return "NO_QUOTE", {}
        if action == "BUY":
            # Only a fresh quote with conservative, fee-inclusive model edge
            # may reach the order journal or a live exchange POST. Never
            # interfere with risk-reducing reversal SELL orders.
            edge = assess_auto_buy_edge(preview, confidence)
            if not edge["approved"]:
                return "NO_EDGE", {"edge_diagnostics": edge}
        if not self._matches_signal(ticker,close,checkpoint,direction,trigger):
            return "NO_QUOTE", {}
        with self._lock:
            if self._state.get("ticker") != ticker or self._state["state"] != "EXECUTING":
                return "NO_QUOTE", {}
        first = not self._attempted(ticker)
        # Record intent BEFORE POST: a crash cannot replay this stage after restart.
        try:
            if self.history_store:
                if first:
                    self.history_store.record_attempt(
                        ticker=ticker, attempted_at_ns=self.now_ns(),
                        outcome=side, amount=armed["amount"], confidence=confidence)
                self.history_store.record_stage_attempt(
                    ticker=ticker, stage=stage, attempted_at_ns=self.now_ns(),
                    outcome=side, amount=amount, confidence=confidence)
            with self._lock:
                self._used.add(ticker)
        except Exception:
            LOG.exception("Cannot journal order attempt")
            return "ERROR", {}
        try:
            result=self.manual.place(preview["preview_id"],ticker)
        except Exception:
            LOG.exception("AutoTrade exchange result unknown")
            if self.history_store:
                try:
                    self.history_store.record_stage_result(
                        ticker=ticker,stage=stage,status="UNKNOWN",
                        order_id=None,fill_count=None)
                    if first:
                        self.history_store.record_result(
                            ticker=ticker,status="UNKNOWN")
                except Exception:
                    LOG.exception("Cannot journal unknown order outcome")
            return "UNKNOWN", {}
        status=result.get("status")
        try:
            fill=Decimal(str(result.get("fill_count")))
        except (InvalidOperation, ValueError):
            status="UNKNOWN"
            fill=Decimal("-1")
        if status not in ("FILLED","PARTIAL","NO_FILL") or (
            status=="NO_FILL" and fill != 0
        ) or (status in ("FILLED","PARTIAL") and fill <= 0):
            status="UNKNOWN"
        if self.history_store:
            try:
                self.history_store.record_stage_result(
                    ticker=ticker,stage=stage,status=status,
                    order_id=result.get("order_id"),fill_count=result.get("fill_count"))
                if first:
                    self.history_store.record_result(
                        ticker=ticker,status=status,order_id=result.get("order_id"),
                        fill_count=result.get("fill_count"))
            except Exception:
                LOG.exception("Exchange result could not be saved")
                return "UNKNOWN", result
        return status,result

    def _status_after_order(self, status, msg):
        if status == "UNKNOWN":
            self._finish("UNKNOWN", msg+"; inspect Kalshi orders, no retry")
        elif status == "ERROR":
            self._finish("ERROR", msg+"; no order submitted")
        elif status == "NO_QUOTE":
            self._set(state="WAITING", message=msg+"; awaiting fresh quote")
        else:
            self._set(state="WAITING", last_result=status, message=msg)

    def tick(self):
        # Non-blocking if a prior tick is still quoting or placing an order.
        if not self._busy.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self._busy.release()

    def _tick(self):
        with self._lock:
            if self._state["state"] != "WAITING":
                return
            armed=dict(self._state)
        now=self.now_ns()
        current=self._snapshot()
        market=self._market(current)
        if market is None:
            return
        ticker,close,checkpoint=armed["ticker"],armed["market_close_ts_ns"],armed["checkpoint_ts_ns"]
        if market[0] != ticker:
            if not armed.get("continuous"):
                self._finish("EXPIRED","Market changed; no order submitted")
                return
            if market[1] <= close:
                return
            new_ticker,new_close,new_checkpoint=market
            skip=(now>=new_checkpoint or current.get("final_forecast") is not None
                  or self._attempted(new_ticker))
            with self._lock:
                if self._state["state"]!="WAITING" or self._state["ticker"]!=ticker:
                    return
                if skip:
                    self._processed.add(new_ticker)
                self._state.update(ticker=new_ticker,market_close_ts_ns=new_close,
                                   checkpoint_ts_ns=new_checkpoint,outcome=None,order_id=None,
                                   trigger=None,early_side=None,early_filled="0",
                                   early_attempts=0,final_attempts=0,
                                   early_retry_after_ns=0,final_retry_after_ns=0,
                                   sold_for_reversal=False,
                                   message=("Waiting for following BTC market" if skip
                                            else "New market found — monitoring live confidence"))
                armed=dict(self._state)
            ticker,close,checkpoint=market
        if market != (ticker,close,checkpoint):
            return
        if ticker in self._processed:
            return
        if (self._attempted(ticker) and not armed.get("early_attempts")
                and not armed.get("final_attempts")):
            self._finish("EXPIRED","Previously attempted market; no repeat")
            return
        if now >= close - MIN_EXECUTION_REMAINING_NS:
            self._finish("EXPIRED","Market closing; no order submitted")
            return
        if now < checkpoint:
            if Decimal(armed.get("early_filled","0")) > 0:
                return  # Existing early position must await final decision.
            if armed.get("early_attempts",0) >= MAX_NO_FILL_ATTEMPTS:
                return
            if now < armed.get("early_retry_after_ns",0):
                return
            signal=self._early_signal(current,now=now,ticker=ticker,
                                      close=close,checkpoint=checkpoint)
            if signal is None:
                return
            direction,confidence=signal
            side="UP" if direction=="ABOVE" else "DOWN"
            stage="EARLY_BUY" if not armed["early_attempts"] else "EARLY_BUY_2"
            self._set(state="EXECUTING",outcome=side,trigger="EARLY",
                      confidence=confidence,message="Early 90% signal — submitting BUY")
            status,result=self._order(armed=armed,stage=stage,action="BUY",
                                      side=side,amount=armed["amount"],units="DOLLARS",
                                      direction=direction,trigger="EARLY",confidence=confidence)
            if status in ("UNKNOWN","ERROR"):
                self._status_after_order(status,"Early BUY not confirmed")
                return
            if status == "NO_EDGE":
                self._set(state="WAITING", early_retry_after_ns=now+5*NS,
                          last_result="NO_EDGE",
                          message="Early BUY skipped: insufficient fee-adjusted model edge")
                return
            if status == "NO_QUOTE":
                self._set(state="WAITING", early_retry_after_ns=now+5*NS,
                          message="Early quote unavailable; waiting for final forecast")
                return
            fill=Decimal(str(result["fill_count"]))
            if status=="NO_FILL" and not armed.get("continuous"):
                self._finish("NO_FILL","Early IOC completed with zero shares")
                return
            self._set(state="WAITING",early_attempts=armed["early_attempts"]+1,
                      last_result=status,early_retry_after_ns=now+RETRY_DELAY_NS,
                      early_side=side if fill>0 else None,
                      early_filled=str(fill) if fill>0 else "0",
                      message=("Early BUY filled; monitoring final forecast"
                               if fill>0 else "Confirmed NO FILL; rechecking fresh price"))
            return
        if now > checkpoint+MAX_LOCK_DELAY_NS:
            self._finish("EXPIRED","Final forecast too late to execute")
            return
        final=self._locked(current,ticker,checkpoint)
        if final is None:
            return
        direction=final["forecast"]
        confidence=final.get("confidence")
        if (direction=="SKIP" or type(confidence) not in (int,float)
                or not math.isfinite(confidence)
                or not 0.69 <= confidence <= 1):
            self._finish("SKIPPED","Final SKIP or confidence below 69%; no order")
            return
        side="UP" if direction=="ABOVE" else "DOWN"
        early_qty=Decimal(armed.get("early_filled","0"))
        early_side=armed.get("early_side")
        if early_qty>0 and early_side==side:
            self._finish("FILLED","Final confirms early BUY; holding existing position")
            return
        if early_qty>0 and early_side!=side and not armed.get("sold_for_reversal"):
            # Sell precisely our early fill, not the user's whole portfolio.
            if not hasattr(self.manual,"_account"):
                self._finish("REVIEW","Cannot verify held shares; reversal blocked")
                return
            try:
                _,net=self.manual._account(ticker,fresh=True)
                held=net if early_side=="UP" else -net
                if held<early_qty:
                    raise ValueError("Position smaller than early fill")
            except Exception:
                self._finish("REVIEW","Early position unverified; no reversal")
                return
            sell_amount=format(early_qty,"f")
            self._set(state="EXECUTING",outcome=early_side,trigger="REVERSAL",
                      message="Opposite final forecast — selling confirmed early shares")
            status,result=self._order(armed=armed,stage="REVERSAL_SELL",action="SELL",
                                     side=early_side,units="SHARES",
                                     amount=sell_amount,direction=direction,
                                     trigger="FINAL",confidence=float(confidence),
                                     sell_exact=early_qty)
            if status not in ("FILLED",):
                self._finish("REVIEW" if status!="UNKNOWN" else "UNKNOWN",
                             "Reversal sell not fully confirmed; opposite BUY blocked")
                return
            if Decimal(str(result["fill_count"])) != early_qty:
                self._finish("REVIEW","Reversal sold fewer shares; opposite BUY blocked")
                return
            # Only after a confirmed full sell may the opposite BUY occur.
            self._set(state="WAITING",sold_for_reversal=True,early_filled="0",
                      message="Early shares sold; checking final direction BUY")
            armed=self.status()
        if early_qty==0 or armed.get("sold_for_reversal"):
            if armed.get("final_attempts",0)>=MAX_NO_FILL_ATTEMPTS:
                self._finish("NO_FILL","No executable final shares after bounded attempts")
                return
            if now < armed.get("final_retry_after_ns",0):
                return
            stage="FINAL_BUY" if not armed["final_attempts"] else "FINAL_BUY_2"
            self._set(state="EXECUTING",outcome=side,trigger="FINAL",
                      confidence=float(confidence),message="Final forecast — submitting BUY")
            status,result=self._order(armed=armed,stage=stage,action="BUY",
                                     side=side,units="DOLLARS",amount=armed["amount"],
                                     direction=direction,trigger="FINAL",
                                     confidence=float(confidence))
            if status in ("UNKNOWN","ERROR"):
                self._status_after_order(status,"Final BUY unconfirmed")
                return
            if status=="NO_EDGE":
                self._finish("SKIPPED",
                             "Final BUY skipped: insufficient fee-adjusted model edge")
                return
            if status=="NO_QUOTE":
                if not armed.get("continuous"):
                    self._finish("EXPIRED","Final quote unavailable or market changed")
                    return
                self._set(state="WAITING",final_retry_after_ns=now+RETRY_DELAY_NS,
                          message="Final quote unavailable; awaiting fresh book")
                return
            self._set(final_attempts=armed.get("final_attempts",0)+1,
                      final_retry_after_ns=now+RETRY_DELAY_NS)
            if status=="NO_FILL":
                if not armed.get("continuous"):
                    self._finish("NO_FILL","Exchange confirmed no executed shares")
                    return
                self._set(state="WAITING",last_result="NO_FILL",
                          message="Confirmed NO FILL; trying another fresh quote")
                return
            self._finish(status,"Final BUY "+status.lower().replace("_"," "),
                         outcome=side,order_id=result.get("order_id"))

    def close(self):
        self._stop.set()
        with self._lock:
            self._generation += 1
            if self._state["state"]=="WAITING":
                self._state.update(state="CANCELLED",continuous=False,
                                   message="Server stopped; AutoTrade disarmed")
