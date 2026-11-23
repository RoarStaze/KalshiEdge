"""Strat V2: frozen-forecast, fee-aware settlement value, PAPER ONLY."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from kalshi_edge.trading.strat_policy import UpQuote
from kalshi_edge.trading.strat_value_policy import evaluate_value_entry
from kalshi_edge.trading.strat_value import ValuePaperTrader

NS = 1_000_000_000
BASE = 1_800_000_000 * NS
TICKER = "KXBTC15M-TEST123"
CLOSE = BASE + 480 * NS
SHA = "a" * 64


def final(ticker=TICKER, close=CLOSE, **overrides):
    f = {
        "market_ticker": ticker, "forecast": "ABOVE", "strike": 65000.0,
        "checkpoint_seconds": 480, "scheduled_ts_ns": close - 480*NS,
        "prediction_ts_ns": close - 480*NS,
        "p_yes": 0.96, "p_no": 0.04, "confidence": 0.96,
        "directional_probability": 0.96,
        "coin_flip_assessment": "LOW", "coin_flip_risk_z": 1.3,
        "model_hash": SHA, "model_version": "promoted-bootstrap",
    }
    f.update(overrides)
    return f


def quote(side="UP", bid="0.63", ask="0.65", ticker=TICKER,
          timestamp=BASE+3*NS, bid_depth="100", ask_depth="100"):
    return UpQuote(ticker, timestamp, D(bid), D(ask),
                   D(bid_depth), D(ask_depth))


def live(now, ticker=TICKER, close=CLOSE, forecast=None, status="LIVE"):
    return {
        "status": status, "market_ticker": ticker,
        "market_close_ts_ns": close, "seconds_remaining": (close-now)/NS,
        "latest_prediction": {
            "market_ticker": ticker, "prediction_ts_ns": now,
        },
        "final_forecast": forecast if forecast is not None else final(ticker,close),
    }


class Feed:
    def __init__(self, yes_bid="0.63", no_bid="0.35", status="open"):
        self.yes_bid, self.no_bid, self.status = yes_bid, no_bid, status
        self.calls = 0
        self.closed = False

    def quote(self, ticker):
        self.calls += 1
        return {"market": {"ticker": ticker, "status": self.status}}, {
            "orderbook_fp": {
                "yes_dollars": [[self.yes_bid, "50"]],
                "no_dollars": [[self.no_bid, "50"]],
            },
        }

    def close(self):
        self.closed = True


def test_value_entry_uses_only_sealed_verified_forecast_and_executable_ask():
    now = BASE + 3*NS
    result = evaluate_value_entry(final(), quote(), now_ns=now, close_ns=CLOSE)
    assert result.action == "BUY"
    assert result.side == "UP"
    assert result.reason == "POSITIVE_GUARDED_SETTLEMENT_VALUE"
    assert result.model_probability == D("0.96")
    assert result.guarded_probability < D("0.96")
    assert result.estimated_edge_per_share > D("0.05")
    assert result.expected_value_status == "MODEL_DEPENDENT_NOT_PROVEN"
    assert result.ask == D("0.65")
    assert result.effective_buy_price == D("0.66")


def test_no_trade_if_price_is_fair_expensive_or_fees_erase_edge():
    now = BASE+3*NS
    for ask in ("0.84", "0.93"):
        q = quote(bid=str(D(ask)-D("0.02")), ask=ask, timestamp=now)
        result = evaluate_value_entry(final(), q, now_ns=now, close_ns=CLOSE)
        assert result.action == "SKIP"
        assert result.reason in ("INSUFFICIENT_NET_VALUE_EDGE", "INSUFFICIENT_PRICE_HEADROOM")
    weak = final(p_yes=0.78, p_no=0.22, confidence=0.78,
                 directional_probability=0.78)
    assert evaluate_value_entry(weak, quote(timestamp=now),now_ns=now,close_ns=CLOSE).action == "SKIP"


@pytest.mark.parametrize("overrides,reason", [
    ({"forecast":"SKIP"}, "INVALID_FROZEN_FORECAST"),
    ({"model_hash":None}, "UNVERIFIED_MODEL_PROVENANCE"),
    ({"model_hash":"abc"}, "UNVERIFIED_MODEL_PROVENANCE"),
    ({"coin_flip_assessment":"HIGH"}, "NEAR_STRIKE_RISK"),
    ({"coin_flip_assessment":"UNAVAILABLE"}, "NEAR_STRIKE_RISK"),
    ({"p_yes":0.8,"p_no":0.15}, "INCONSISTENT_MODEL_PROBABILITIES"),
    ({"p_yes":0.1,"p_no":0.9}, "MODEL_DIRECTION_CONFLICT"),
    ({"checkpoint_seconds":240}, "NOT_480_SECOND_FROZEN_CHECKPOINT"),
    ({"prediction_ts_ns":BASE+4*NS}, "CHECKPOINT_TIMESTAMP_MISMATCH"),
    ({"market_ticker":"KXBTC15M-WRONG"}, "QUOTE_MARKET_MISMATCH"),
])
def test_frozen_forecast_fail_closed(overrides, reason):
    outcome = evaluate_value_entry(final(**overrides), quote(),
                                   now_ns=BASE+3*NS, close_ns=CLOSE)
    assert outcome.action == "SKIP"
    assert outcome.reason == reason


def test_old_checkpoint_or_bad_quote_is_not_tradeable():
    delayed = evaluate_value_entry(final(), quote(timestamp=BASE+31*NS),
                                    now_ns=BASE+31*NS, close_ns=CLOSE)
    assert delayed.reason == "ENTRY_WINDOW_EXPIRED"
    stale = evaluate_value_entry(final(), quote(timestamp=BASE+1*NS),
                                now_ns=BASE+7*NS, close_ns=CLOSE)
    assert stale.reason == "STALE_QUOTE"
    wide = evaluate_value_entry(final(), quote(bid="0.59",ask="0.65"),
                               now_ns=BASE+3*NS,close_ns=CLOSE)
    assert wide.reason == "SPREAD_TOO_WIDE"
    wrong = evaluate_value_entry(final(), quote(ticker="KXBTC15M-OTHER"),
                                now_ns=BASE+3*NS,close_ns=CLOSE)
    assert wrong.reason == "QUOTE_MARKET_MISMATCH"
    empty = evaluate_value_entry(final(),quote(ask_depth="0"),
                                now_ns=BASE+3*NS,close_ns=CLOSE)
    assert empty.reason == "NO_EXECUTABLE_DEPTH"


def test_down_uses_no_side_ask_and_declines_wrong_direction():
    now=BASE+3*NS
    f=final(forecast="BELOW",p_yes=0.04,p_no=0.96,
            directional_probability=0.96)
    q=quote("DOWN",bid="0.63",ask="0.65",timestamp=now)
    result=evaluate_value_entry(f,q,now_ns=now,close_ns=CLOSE)
    assert result.action=="BUY" and result.side=="DOWN"
    assert result.ask==D("0.65")


def test_value_paper_one_market_one_position_and_official_settlement(tmp_path):
    now = [BASE]
    market = [TICKER]
    closing = [CLOSE]

    def read():
        return live(now[0],ticker=market[0],close=closing[0])

    feed=Feed()
    results={TICKER:"yes"}
    trader=ValuePaperTrader(tmp_path,read,client=feed,clock=lambda:now[0],
                            settlement_reader=lambda t:results.get(t))
    assert trader.status()["enabled"] is False
    trader.arm("1.00")
    now[0]=BASE+3*NS
    trader.tick()
    pos=trader.ledger.open_position()
    assert pos is not None and pos["ticker"]==TICKER and pos["side"]=="UP"
    assert D(pos["total_paid"])<=D("1")
    assert trader.status()["signal"]["action"]=="BUY"
    trader.tick()
    assert trader.ledger.ticker_count(TICKER)==1
    assert feed.calls==1
    now[0]=CLOSE+NS
    trader.tick()
    row=trader.ledger.recent()[0]
    assert row["status"]=="SETTLED"
    assert row["exit_reason"]=="OFFICIAL_SETTLEMENT"
    assert D(row["paper_net_pnl"])==D(row["quantity"])-D(row["total_paid"])
    assert trader.status()["settled_count"]==1
    assert trader.status()["live_orders_enabled"] is False
    trader.close()
    assert feed.closed is True
    reboot=ValuePaperTrader(tmp_path,read,client=Feed(),clock=lambda:now[0])
    assert reboot.status()["enabled"] is False
    assert reboot.ledger.ticker_count(TICKER)==1
    reboot.close()


def test_value_trader_preserves_v1_paper_ledger_and_never_guesses_settlement(tmp_path):
    from kalshi_edge.trading.paper_scalper import PaperScalpLedger
    prior=PaperScalpLedger(tmp_path/"trading"/"strat_up_paper.sqlite3",D("100"))
    prior.insert(ticker=TICKER,side="UP",quantity=D("1"),price=D("0.70"),
                 fee=D("0.02"),paid=D("0.72"),now_ns=BASE,close_ns=CLOSE)
    now=[BASE+3*NS]
    trader=ValuePaperTrader(tmp_path,lambda:live(now[0]),
                            client=Feed(),clock=lambda:now[0],
                            settlement_reader=lambda _:None)
    trader.arm("1.00")
    trader.tick()
    assert trader.ledger.open_position() is not None
    now[0]=CLOSE+NS
    trader.tick()
    assert trader.ledger.open_position() is not None
    assert trader.status()["last_signal"]=="WAITING_FOR_VERIFIED_SETTLEMENT"
    assert prior.open_position() is not None
    assert trader.ledger.path != prior.path
    trader.close()


def test_no_value_buy_when_feed_stale_final_missing_or_quote_unavailable(tmp_path):
    now=[BASE+3*NS]
    def invalid():
        return live(now[0],forecast=final(),status="DEGRADED")
    feed=Feed()
    trader=ValuePaperTrader(tmp_path,invalid,client=feed,clock=lambda:now[0])
    trader.arm("1.00")
    trader.tick()
    assert feed.calls==0
    assert trader.status()["last_signal"]=="UNHEALTHY_LIVE_FEED"
    assert trader.ledger.recent()==[]
    trader.close()


def test_daily_risk_limit_and_bankroll_stop_repeated_losses(tmp_path):
    now=[BASE+3*NS]
    trader=ValuePaperTrader(tmp_path,lambda:live(now[0]),
                            client=Feed(),clock=lambda:now[0])
    trader.arm("1.00")
    for i in range(4):
        t=BASE
        trader.ledger.insert(ticker=f"KXBTC15M-LOSS{i}",
            side="UP",quantity=D("1"),price=D("0.65"),fee=D("0.01"),
            paid=D("0.66"),now_ns=t,close_ns=CLOSE)
        pos=trader.ledger.open_position()
        trader.ledger.close(pos["id"],price=D("0"),fee=D("0"),
                            now_ns=t+NS,reason="OFFICIAL_SETTLEMENT",
                            pnl=D("-0.66"),settled=True)
    trader.tick()
    assert trader.ledger.open_position() is None
    assert trader.status()["last_signal"]=="DAILY_RISK_LIMIT"
    trader.close()


def test_v2_page_uses_separate_value_strategy_and_unvalidated_profit_label():
    from kalshi_edge.bootstrap.strat_ui import render_value_strat_page
    html=render_value_strat_page()
    for token in ("Frozen forecast", "Conservative probability", "Expected net value",
                  "Risk limit", "No verified profitable edge",
                  "/api/strat/status", "/api/strat/arm",
                  "Verified settlement", "Legacy momentum"):
        assert token in html
    assert "PAPER ONLY" in html
    assert "No verified profitable edge" in html



def test_public_market_strike_and_close_must_match_frozen_signal_when_available(tmp_path):
    from datetime import datetime, timezone
    now=[BASE+3*NS]
    class ConflictingFeed(Feed):
        def __init__(self, mismatch):
            super().__init__()
            self.mismatch=mismatch
        def quote(self,ticker):
            market,book=super().quote(ticker)
            market["market"]["floor_strike"]=(
                "66000.00" if self.mismatch=="strike" else "65000.00"
            )
            market["market"]["close_time"]=datetime.fromtimestamp(
                (CLOSE + (60*NS if self.mismatch=="close" else 0))/NS,
                timezone.utc
            ).isoformat()
            return market,book

    for mismatch in ("strike","close"):
        root=tmp_path/mismatch
        trader=ValuePaperTrader(
            root,lambda:live(now[0]),client=ConflictingFeed(mismatch),
            clock=lambda:now[0],
        )
        trader.arm("1.00")
        trader.tick()
        assert trader.ledger.open_position() is None
        assert trader.status()["last_signal"]=="PUBLIC_ORDERBOOK_UNAVAILABLE"
        trader.close()
