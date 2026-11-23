"""Strat V2: frozen-checkpoint, fee-aware settlement-value PAPER trader.

Never imports authenticated trading clients or submits orders. No claimed
profitability; all fills are marked hypothetical and all settlement outcomes
must be verified through the existing local official-settlement reader.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
import logging
import threading
import time
from typing import Callable

from .paper import PublicMarketClient
from .paper_scalper import PaperScalpLedger
from .scalp_policy import quote_from_kalshi
from .strat_policy import UpQuote
from .strat_paper import standard_taker_fee_estimate
from .strat_value_policy import (
    ValueDecision, evaluate_value_entry, SLIPPAGE,
)

D = Decimal
NS = 1_000_000_000
LOG = logging.getLogger(__name__)
INITIAL_CAPITAL = D("100.00")
MAX_BUDGET = D("2.00")
MAX_POSITION_EQUITY_FRACTION = D("0.01")
DAILY_LOSS_CAP = D("2.00")
SCAN_SECONDS = 0.75
FINAL_LOCK_SECONDS = 480


def _cash(value: D) -> str:
    return format(value.quantize(D("0.0001")), "f")


class ValuePaperTrader:
    """One value-filtered hypothetical entry per market, held to settlement."""

    def __init__(
        self, root: Path, read_live: Callable[[], dict], *,
        client=None, clock: Callable[[], int] = time.time_ns,
        settlement_reader: Callable[[str], str | None] | None = None,
    ):
        self.root = Path(root).resolve()
        self.ledger = PaperScalpLedger(
            self.root / "trading" / "strat_value_paper.sqlite3",
            INITIAL_CAPITAL,
        )
        self.legacy_ledger_path = self.root / "trading" / "strat_up_paper.sqlite3"
        self.client = client if client is not None else PublicMarketClient()
        self.clock = clock
        self.read_live = read_live
        self.settlement_reader = settlement_reader
        self._lock = threading.RLock()
        self._enabled = False  # Never rearm automatically on restart.
        self._budget = D("1.00")
        self._last_signal = "DISARMED"
        self._market_ticker: str | None = None
        self._decision: ValueDecision | None = None
        self._quote_info: dict | None = None
        self._last_attempt_ns: int | None = None

    def _stats(self, now: int) -> dict:
        rows=self.ledger.recent(100000)
        settled=[r for r in rows if r["status"]=="SETTLED"]
        net=sum((D(r["paper_net_pnl"]) for r in settled),D("0"))
        today=datetime.fromtimestamp(now/NS,timezone.utc).date()
        losses=sum((
            -D(r["paper_net_pnl"]) for r in settled
            if D(r["paper_net_pnl"]) < 0 and
            datetime.fromtimestamp(r["exit_ns"]/NS,timezone.utc).date()==today
        ), D("0"))
        open_pos=self.ledger.open_position()
        cash=max(D("0"),self.ledger.initial_bankroll+net
                 -(D(open_pos["total_paid"]) if open_pos else D("0")))
        return {
            "cash":cash, "net":net, "daily_losses":losses,
            "open":open_pos, "settled_count":len(settled),
            "wins":sum(D(r["paper_net_pnl"])>0 for r in settled),
        }

    def _settle(self, row: dict, now: int) -> None:
        result=None
        if self.settlement_reader is not None:
            try:
                result=self.settlement_reader(row["ticker"])
            except Exception:
                LOG.exception("V2 official settlement unavailable for %s",row["ticker"])
        if result not in ("yes","no"):
            self._last_signal="WAITING_FOR_VERIFIED_SETTLEMENT"
            return
        winner="UP" if result=="yes" else "DOWN"
        qty=D(row["quantity"])
        payout=qty if row["side"]==winner else D("0")
        self.ledger.close(
            row["id"],price=D("1") if row["side"]==winner else D("0"),
            fee=D("0"),now_ns=now,reason="OFFICIAL_SETTLEMENT",
            pnl=payout-D(row["total_paid"]),settled=True,
        )
        self._last_signal="SETTLED_"+row["side"]+"_"+result.upper()

    def _quote(self,ticker:str,side:str)->UpQuote:
        market,book=self.client.quote(ticker)
        meta=market.get("market") if isinstance(market,dict) else None
        if (not isinstance(meta,dict) or meta.get("ticker")!=ticker or
                meta.get("status") not in ("active","open")):
            raise ValueError("Unverified public market identity/status")
        receipt=self.clock()  # after HTTP response, never before
        q=quote_from_kalshi(book,side,timestamp_ns=receipt)
        return UpQuote(ticker,receipt,q.bid,q.ask,q.bid_available,q.ask_available)

    def arm(
        self, amount: str, *,
        exploratory_ignore_negative_evidence: bool = False,
    ) -> dict:
        """Legacy HTTP flag accepted ONLY when false; never disable EV gates."""
        if type(exploratory_ignore_negative_evidence) is not bool or (
                exploratory_ignore_negative_evidence):
            raise ValueError("V2 value entry gates cannot be bypassed")
        with self._lock:
            try:
                budget=D(amount)
            except (InvalidOperation,ValueError,TypeError) as exc:
                raise ValueError("Invalid paper budget") from exc
            if (not budget.is_finite() or budget.as_tuple().exponent < -2 or
                    not D("0.10")<=budget<=MAX_BUDGET):
                raise ValueError("V2 paper max stake must be $0.10–$2.00")
            self._budget=budget
            self._enabled=True
            self._last_signal="WAITING_FOR_VERIFIED_480S_CHECKPOINT"
            return self.status()

    def pause(self)->dict:
        with self._lock:
            self._enabled=False
            self._last_signal=("ENTRIES_PAUSED_HOLDING_TO_SETTLEMENT"
                if self.ledger.open_position() else "PAUSED")
            return self.status()

    def _execute_paper(self, decision:ValueDecision, quote:UpQuote,
                       ticker:str,close_ns:int,now:int,stats:dict)->None:
        """Never assume a maker queue fill. Taker price+slippage, depth capped."""
        price=decision.effective_buy_price
        assert price is not None and decision.side is not None
        if quote.ask_depth <= 0:
            self._last_signal="NO_ASK_DEPTH"
            return
        risk_budget=min(self._budget,MAX_BUDGET,
                        stats["cash"]*MAX_POSITION_EQUITY_FRACTION)
        if risk_budget < D("0.01"):
            self._last_signal="INSUFFICIENT_PAPER_BANKROLL"
            return
        maximum=min(quote.ask_depth,risk_budget/price,D("1000"))
        units=int((maximum*100).to_integral_value(rounding=ROUND_FLOOR))
        lo,hi=0,max(0,units)
        while lo<hi:
            mid=(lo+hi+1)//2
            qty=D(mid)/100
            cost=price*qty+standard_taker_fee_estimate(price,qty)
            if cost<=risk_budget:
                lo=mid
            else:
                hi=mid-1
        if lo<=0:
            self._last_signal="DEPTH_OR_BUDGET_TOO_SMALL"
            return
        qty=D(lo)/100
        fee=standard_taker_fee_estimate(price,qty)
        paid=price*qty+fee
        self.ledger.insert(
            ticker=ticker,side=decision.side,quantity=qty,
            price=price,fee=fee,paid=paid,
            now_ns=now,close_ns=close_ns,
        )
        self._last_signal="PAPER_BOUGHT_"+decision.side+"_GUARDED_SETTLEMENT_VALUE"

    def tick(self, now_ns:int|None=None)->dict:
        with self._lock:
            now=self.clock() if now_ns is None else now_ns
            stats=self._stats(now)
            opened=stats["open"]
            if opened is not None:
                self._market_ticker=opened["ticker"]
                if now>=opened["close_ns"]:
                    self._settle(opened,now)
                else:
                    self._last_signal="HOLDING_TO_VERIFIED_SETTLEMENT"
                return self.status()
            if not self._enabled:
                self._last_signal="DISARMED_OR_PAUSED"
                return self.status()
            if stats["daily_losses"]>=DAILY_LOSS_CAP:
                self._last_signal="DAILY_RISK_LIMIT"
                return self.status()
            try:
                snapshot=self.read_live()
            except Exception:
                snapshot=None
            if not isinstance(snapshot,dict) or snapshot.get("status")!="LIVE":
                self._last_signal="UNHEALTHY_LIVE_FEED"
                return self.status()
            ticker=snapshot.get("market_ticker")
            close=snapshot.get("market_close_ts_ns")
            latest=snapshot.get("latest_prediction")
            if (not isinstance(ticker,str) or
                    not ticker.startswith("KXBTC15M-") or
                    not ticker.replace("-","").isalnum() or
                    type(close) is not int or
                    not 0<close-now<=900*NS or
                    not isinstance(latest,dict) or
                    latest.get("market_ticker")!=ticker or
                    type(latest.get("prediction_ts_ns")) is not int or
                    not 0<=now-latest["prediction_ts_ns"]<=8*NS):
                self._last_signal="WAITING_FOR_FRESH_MARKET"
                return self.status()
            self._market_ticker=ticker
            if self.ledger.ticker_count(ticker):
                self._last_signal="ALREADY_ENTERED_THIS_MARKET"
                return self.status()
            final=snapshot.get("final_forecast")
            if not isinstance(final,dict):
                self._last_signal="WAITING_FOR_VERIFIED_480S_CHECKPOINT"
                return self.status()
            if final.get("forecast") not in ("ABOVE","BELOW"):
                self._last_signal="FINAL_FORECAST_SKIP"
                return self.status()
            locked_at=final.get("prediction_ts_ns")
            if type(locked_at) is not int or not 0<=now-locked_at<=30*NS:
                self._last_signal="ENTRY_WINDOW_EXPIRED_OR_INVALID"
                return self.status()
            side="UP" if final["forecast"]=="ABOVE" else "DOWN"
            try:
                quote=self._quote(ticker,side)
            except Exception:
                LOG.debug("V2 public book unavailable",exc_info=True)
                self._last_signal="PUBLIC_ORDERBOOK_UNAVAILABLE"
                return self.status()
            observed=quote.observed_ns
            decision=evaluate_value_entry(
                final,quote,now_ns=observed,close_ns=close,
            )
            self._decision=decision
            self._last_attempt_ns=observed
            self._quote_info={
                "side":side,"bid":str(quote.bid),"ask":str(quote.ask),
                "bid_depth":str(quote.bid_depth),"ask_depth":str(quote.ask_depth),
                "received_ns":observed,
            }
            if decision.action!="BUY":
                self._last_signal=decision.reason
                return self.status()
            if self._stats(observed)["daily_losses"]>=DAILY_LOSS_CAP:
                self._last_signal="DAILY_RISK_LIMIT"
                return self.status()
            if self.ledger.ticker_count(ticker)>0:
                self._last_signal="ALREADY_ENTERED_THIS_MARKET"
                return self.status()
            self._execute_paper(decision,quote,ticker,close,observed,stats)
            return self.status()

    def status(self)->dict:
        with self._lock:
            now=self.clock()
            st=self._stats(now)
            rows=self.ledger.recent(25)
            return {
                "mode":"FROZEN_480S_SETTLEMENT_VALUE_PAPER",
                "version":2, "enabled":self._enabled,
                "live_orders_enabled":False,
                "profitability_status":"NOT_VALIDATED_PROFITABLE",
                "last_signal":self._last_signal,
                "market_ticker":self._market_ticker,
                "budget_dollars":_cash(self._budget),
                "max_position_fraction":"0.01",
                "max_daily_loss_dollars":_cash(DAILY_LOSS_CAP),
                "daily_realized_loss_dollars":_cash(st["daily_losses"]),
                "virtual_cash_dollars":_cash(st["cash"]),
                "paper_net_pnl_dollars":_cash(st["net"]),
                "settled_count":st["settled_count"],
                "settled_winners":st["wins"],
                "open_position":st["open"],
                "round_trips":rows,
                "signal":self._decision.as_dict() if self._decision else None,
                "quote":self._quote_info,
                "last_attempt_ns":self._last_attempt_ns,
                "legacy_ledger_path":str(self.legacy_ledger_path),
                "legacy_ledger_present":self.legacy_ledger_path.is_file(),
                "entry_rules":{
                    "required_checkpoint_seconds":480,
                    "max_entry_lag_seconds":30,
                    "min_original_probability":"0.78",
                    "uncertainty_shrink_toward_half":"0.25",
                    "additional_edge_buffer_per_contract":"0.05",
                    "max_bid_ask_spread":"0.03",
                    "modeled_entry_slippage":"0.01",
                    "fee_safety_multiplier":"1.5",
                    "new_trades_per_market":1,
                },
                "warning":(
                    "PAPER ONLY. Fee-aware model-dependent positive estimated "
                    "value is not a demonstrated profit edge. Current confidence "
                    "shrink and 5c buffer are heuristics, not calibrated bounds. "
                    "Taker fills, fees and exact settlement are unverified until "
                    "independently reconciled. Losses can exceed wins. No live orders."
                ),
            }

    def replay_audit(self)->dict:
        with self._lock:
            st=self._stats(self.clock())
            return {
                "kind":"V2_FROZEN_FORECAST_SETTLEMENT_RESULTS",
                "status":"NOT_VALIDATED_PROFITABLE",
                "settled_count":st["settled_count"],
                "winners":st["wins"],
                "paper_net_pnl_dollars":_cash(st["net"]),
                "open_or_unverified":bool(st["open"]),
                "warning":"No out-of-sample verified net edge or actual order-fill proof",
            }

    def run_forever(self,stop:threading.Event)->None:
        while not stop.wait(SCAN_SECONDS):
            try:
                self.tick()
            except Exception:
                LOG.exception("Strat V2 paper cycle failed")

    def close(self)->None:
        self.client.close()
