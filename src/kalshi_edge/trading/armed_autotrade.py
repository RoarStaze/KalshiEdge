"""Explicit, market-scoped AutoTrade: final eight-minute forecast only.

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
import uuid
from typing import Callable

from .autotrade_history import AutoTradeHistory
from .autotrade_pnl import enrich_history
from .autotrade_scorecard import summarize_autotrade_performance
from .profit_intelligence import build_profit_intelligence
from .auto_edge import assess_auto_buy_edge, suggest_auto_stake
from .autotrade_execution_audit import sanitize_quote_snapshot
from .manual_trade import MAX_REQUEST_DOLLARS, OrderNotSubmitted, ManualTrader
from kalshi_edge.bootstrap.final_forecast import FINAL_CHECKPOINT_SECONDS

LOG = logging.getLogger(__name__)
NS = 1_000_000_000
MAX_LOCK_DELAY_NS = 30 * NS
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
        self._profit_root = Path(history_path).parent.parent if history_path else None
        self._history_fetch_lock = threading.Lock()
        self._history_cache = None

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _profit_report(self, entries: list[dict]) -> dict:
        # Read-only official settlement evidence; never used to authorize a BUY.
        forecasts = []
        if self._profit_root is not None:
            try:
                from kalshi_edge.bootstrap.final_history import read_final_history
                forecasts = read_final_history(self._profit_root)["forecasts"]
            except Exception:
                LOG.exception("Profit intelligence settlement evidence unavailable")
        return build_profit_intelligence(forecasts, entries)

    def history(self) -> dict:
        if self.history_store is None:
            return {"entries": [], "summary": summarize_autotrade_performance([]),
                    "profit_intelligence": self._profit_report([])}
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
                entries = [dict(entry) for entry in cached["entries"]]
                return {"entries": entries, "summary": summarize_autotrade_performance(entries),
                        "profit_intelligence": cached["profit_intelligence"]}
            client = getattr(self.manual, "client", None)
            enriched = enrich_history(rows, client)
            entries = [dict(entry) for entry in enriched]
            report = self._profit_report(entries)
            self._history_cache = {"fingerprint": fingerprint, "at": time.monotonic(),
                                   "entries": enriched, "profit_intelligence": report}
            return {"entries": entries, "summary": summarize_autotrade_performance(entries),
                    "profit_intelligence": report}

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
                "final_attempts": 0, "final_retry_after_ns": 0,
                "last_quote_reason": None, "last_result": None,
                "latest_opportunity": None,
                "message": ("Waiting for next BTC market" if skip
                            else "Armed — waiting for eight-minute final forecast"),
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
                "FILLED", "PARTIAL", "NO_FILL", "SKIPPED", "EXPIRED", "NO_QUOTE", "NOT_SUBMITTED"
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
        # A live continuous prediction can NEVER authorize an AUTO order.
        if trigger != "FINAL":
            return False
        now = self.now_ns()
        live = self._snapshot()
        if self._market(live) != (ticker, close, checkpoint):
            return False
        if (now < checkpoint or now > checkpoint + MAX_LOCK_DELAY_NS
                or now >= close - MIN_EXECUTION_REMAINING_NS):
            return False
        final = self._locked(live, ticker, checkpoint)
        return final is not None and final.get("forecast") == direction

    def _order(self, *, armed: dict, stage: str, action: str, side: str,
               amount: str, units: str, direction: str, trigger: str,
               confidence: float, sell_exact: Decimal | None = None):
        """Return (status, exchange data). NONE/NO_QUOTE means no order sent."""
        ticker=armed["ticker"]
        close=armed["market_close_ts_ns"]
        checkpoint=armed["checkpoint_ts_ns"]
        try:
            preview=self.manual.preview(ticker,action,side,units,amount)
            preview_received_at_ns = self.now_ns()
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
            # The production ManualTrader has a second, mandatory
            # fresh-quote value guard immediately before the signed POST.
            # Legacy fake exchanges retain the historical interface.
            value_first = callable(getattr(self.manual, "place_auto_value", None))
            edge = assess_auto_buy_edge(preview, confidence)
            evidence_preview = preview
            maker_approved = False
            # A negative taker-ask EV is NOT sufficient to reject a
            # cheaper maker limit. Check its independently sized EV before
            # journaling, keeping malformed taker quotes fail-closed.
            if (value_first and stage == "FINAL_BUY"
                    and edge["reason"] != "INVALID_QUOTE"):
                preflight = getattr(self.manual, "preview_auto_maker", None)
                if callable(preflight):
                    try:
                        maker_plan = preflight(preview, confidence)
                    except Exception:
                        maker_plan = None
                    if (isinstance(maker_plan, dict)
                            and isinstance(maker_plan.get("edge_diagnostics"), dict)
                            and maker_plan["edge_diagnostics"].get("approved") is True):
                        maker_approved = True
                        edge = maker_plan["edge_diagnostics"]
                        # Journal the actual contemplated maker limit rather
                        # than the rejected taker ask. This is still only a
                        # quote; authenticated fills remain authoritative.
                        evidence_preview = {**preview, **{
                            key: maker_plan[key]
                            for key in ("unit_price", "count",
                                        "fee_estimate", "amount_estimate")
                        }}
            if value_first and edge["approved"] and not maker_approved:
                sized = suggest_auto_stake(preview, confidence, amount)
                if sized is None:
                    edge = {**edge, "approved": False, "reason": "INSUFFICIENT_EDGE"}
                elif sized < Decimal(amount):
                    try:
                        amount = format(sized, ".2f")
                        preview = self.manual.preview(
                            ticker, action, side, units, amount,
                        )
                        preview_received_at_ns = self.now_ns()
                        if (preview.get("ticker") != ticker
                                or preview.get("action") != action
                                or preview.get("outcome") != side
                                or not isinstance(preview.get("preview_id"), str)):
                            return "NO_QUOTE", {}
                        edge = assess_auto_buy_edge(preview, confidence)
                    except Exception:
                        return "NO_QUOTE", {}
            quote_evidence = sanitize_quote_snapshot(
                evidence_preview, edge, preview_received_at_ns=preview_received_at_ns,
            )
            if value_first:
                route = ("MAKER_FIRST" if maker_approved else
                         "TAKER_IOC" if edge["approved"] else "NO_TRADE")
                self._set(latest_opportunity={
                    "ticker": ticker, "route": route,
                    "reason": edge.get("reason"),
                    "expected_net_dollars": edge.get("expected_net_dollars"),
                    "model_win_probability": edge.get("model_win_probability"),
                    "quote_received_at_ns": preview_received_at_ns,
                    "estimated_only": True,
                })
            if edge["reason"] == "INVALID_QUOTE" or (value_first and not edge["approved"]):
                if self.history_store is not None:
                    try:
                        self.history_store.record_quote_rejection(
                            ticker=ticker, stage=stage, reason=edge["reason"],
                            observed_at_ns=preview_received_at_ns,
                            quote=quote_evidence,
                        )
                    except Exception:
                        LOG.warning("Rejected quote evidence unavailable", exc_info=True)
                return ("NO_QUOTE" if edge["reason"] == "INVALID_QUOTE"
                        else "NO_EDGE"), {"edge_diagnostics": edge}
            if not edge["approved"]:
                LOG.warning(
                    "Legacy fake AutoTrade proceeding despite %s; "
                    "production uses mandatory fresh-price gate",
                    edge["reason"],
                )
        else:
            quote_evidence = sanitize_quote_snapshot(
                preview, None, preview_received_at_ns=preview_received_at_ns,
            )
        if not self._matches_signal(ticker,close,checkpoint,direction,trigger):
            return "NO_QUOTE", {}
        with self._lock:
            if self._state.get("ticker") != ticker or self._state["state"] != "EXECUTING":
                return "NO_QUOTE", {}
        first = not self._attempted(ticker)
        # The same exchange client ID must survive a lost POST response.
        client_order_id = (str(uuid.uuid4()) if action == "BUY" and
                           isinstance(self.manual, ManualTrader) else None)
        # Record intent BEFORE POST: a crash cannot replay this stage after restart.
        try:
            if self.history_store:
                if first:
                    self.history_store.record_attempt(
                        ticker=ticker, attempted_at_ns=self.now_ns(),
                        outcome=side, amount=armed["amount"], confidence=confidence)
                self.history_store.record_stage_attempt(
                    ticker=ticker, stage=stage, attempted_at_ns=self.now_ns(),
                    outcome=side, amount=amount, confidence=confidence,
                    quote=quote_evidence, client_order_id=client_order_id)
            with self._lock:
                self._used.add(ticker)
        except Exception:
            LOG.exception("Cannot journal order attempt")
            return "ERROR", {}
        try:
            # Armed final orders use the fresh, budget-capped AutoTrade path.
            # Legacy test doubles without place_auto retain their fake place.
            value_place = getattr(self.manual, "place_auto_value", None)
            auto_place = getattr(self.manual, "place_auto", None)
            if action == "BUY" and callable(value_place):
                kwargs = {
                    "confidence": confidence,
                    "prefer_maker": stage == "FINAL_BUY" and maker_approved,
                }
                if client_order_id is not None:
                    kwargs["client_order_id"] = client_order_id
                result = value_place(preview["preview_id"], ticker, **kwargs)
            elif callable(auto_place):
                result = auto_place(preview["preview_id"],ticker)
            else:
                result = self.manual.place(preview["preview_id"],ticker)
        except OrderNotSubmitted as exc:
            # The manual trader has confirmed failure before POST or a
            # definite exchange rejection. Do not mislabel as UNKNOWN.
            reason = str(exc)[:160]
            LOG.warning("AutoTrade order not submitted: %s", reason)
            if self.history_store:
                try:
                    self.history_store.record_stage_result(
                        ticker=ticker,stage=stage,status="NOT_SUBMITTED",
                        order_id=None,fill_count=None)
                    if first:
                        self.history_store.record_result(
                            ticker=ticker,status="NOT_SUBMITTED")
                except Exception:
                    LOG.exception("Cannot journal unsubmitted order outcome")
                    return "ERROR", {}
            return "NOT_SUBMITTED", {"reason": reason}
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
            armed = dict(self._state)
        now = self.now_ns()
        current = self._snapshot()
        market = self._market(current)
        if market is None:
            return

        ticker = armed["ticker"]
        close = armed["market_close_ts_ns"]
        checkpoint = armed["checkpoint_ts_ns"]
        if market[0] != ticker:
            if not armed.get("continuous"):
                self._finish("EXPIRED", "Market changed; no order submitted")
                return
            if market[1] <= close:
                return
            new_ticker, new_close, new_checkpoint = market
            skip = (now >= new_checkpoint
                    or current.get("final_forecast") is not None
                    or self._attempted(new_ticker))
            with self._lock:
                if self._state["state"] != "WAITING" or self._state["ticker"] != ticker:
                    return
                if skip:
                    self._processed.add(new_ticker)
                self._state.update(
                    ticker=new_ticker, market_close_ts_ns=new_close,
                    checkpoint_ts_ns=new_checkpoint,
                    outcome=None, order_id=None, trigger=None,
                    final_attempts=0, final_retry_after_ns=0,
                    last_quote_reason=None, latest_opportunity=None,
                    message=("Waiting for following BTC market" if skip
                             else "New market found — waiting for eight-minute final"),
                )
                armed = dict(self._state)
            ticker, close, checkpoint = market
        if market != (ticker, close, checkpoint) or ticker in self._processed:
            return
        if self._attempted(ticker) and not armed.get("final_attempts"):
            self._finish("EXPIRED", "Previously attempted market; no repeat")
            return
        if now < checkpoint:
            # No early confidence trigger: never preview or submit an order
            # before the immutable 8-minute final forecast is sealed.
            return
        if now > checkpoint + MAX_LOCK_DELAY_NS or now >= close - MIN_EXECUTION_REMAINING_NS:
            rejected = armed.get("last_quote_reason")
            self._finish(
                "EXPIRED",
                (f"No profitable entry in final quote window ({rejected}); no order sent"
                 if rejected and not self._attempted(ticker)
                 else "Final forecast too late to execute"),
            )
            return

        final = self._locked(current, ticker, checkpoint)
        if final is None:
            return
        direction = final["forecast"]
        confidence = final.get("confidence")
        if (direction == "SKIP" or type(confidence) not in (int, float)
                or not math.isfinite(confidence)
                or not 0.69 <= confidence <= 1):
            self._finish("SKIPPED", "Final SKIP or confidence below 69%; no order")
            return
        if armed.get("final_attempts", 0) >= MAX_NO_FILL_ATTEMPTS:
            self._finish("NO_FILL", "No executed shares after bounded retries")
            return
        if now < armed.get("final_retry_after_ns", 0):
            return

        side = "UP" if direction == "ABOVE" else "DOWN"
        stage = "FINAL_BUY" if not armed["final_attempts"] else "FINAL_BUY_2"
        self._set(state="EXECUTING", outcome=side, trigger="FINAL",
                  confidence=float(confidence),
                  message="Eight-minute final forecast — submitting BUY")
        status, result = self._order(
            armed=armed, stage=stage, action="BUY", side=side,
            units="DOLLARS", amount=armed["amount"],
            direction=direction, trigger="FINAL",
            confidence=float(confidence),
        )
        if status in ("UNKNOWN", "ERROR"):
            self._status_after_order(status, "Final BUY unconfirmed")
            return
        if status == "NOT_SUBMITTED":
            self._finish("NOT_SUBMITTED",
                         "Final BUY not sent: " + result.get("reason", "pre-submit check failed"))
            return
        if status == "NO_EDGE":
            # Keep the rejection cause visible instead of overwriting it
            # with a misleading generic "too late" once the window ends.
            reason = result.get("edge_diagnostics", {}).get("reason", "NO_EDGE")
            self._set(state="WAITING", final_retry_after_ns=now + RETRY_DELAY_NS,
                      last_quote_reason=reason,
                      message=f"Waiting for positive-value quote ({reason}); no order sent")
            return
        if status == "NO_QUOTE":
            if not armed.get("continuous"):
                self._finish("EXPIRED", "Final quote unavailable or market changed")
                return
            self._set(state="WAITING", final_retry_after_ns=now + RETRY_DELAY_NS,
                      message="Final quote unavailable; awaiting fresh book")
            return

        attempts = armed.get("final_attempts", 0) + 1
        self._set(final_attempts=attempts,
                  final_retry_after_ns=now + RETRY_DELAY_NS)
        if status == "NO_FILL":
            if not armed.get("continuous") or attempts >= MAX_NO_FILL_ATTEMPTS:
                self._finish("NO_FILL", "Exchange confirmed no executed shares")
                return
            self._set(state="WAITING", last_result="NO_FILL",
                      message="Confirmed NO FILL; retrying fresh final quote")
            return
        # FILLED or PARTIAL is terminal for this round. Never switch sides,
        # sell an earlier position, or submit another entry after a fill.
        self._finish(status, "Final BUY " + status.lower().replace("_", " "),
                     outcome=side, order_id=result.get("order_id"))

    def close(self):
        self._stop.set()
        with self._lock:
            self._generation += 1
            if self._state["state"]=="WAITING":
                self._state.update(state="CANCELLED",continuous=False,
                                   message="Server stopped; AutoTrade disarmed")
