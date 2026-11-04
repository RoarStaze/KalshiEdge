"""Regression: deterministic pre-submit failures are not uncertain exchange fills.

Uses in-memory fake exchange operations; never submits real money orders.
"""
from types import SimpleNamespace

import pytest

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
from kalshi_edge.trading.manual_trade import (
    ManualTrader, TradingError, OrderNotSubmitted,
)

NS = 1_000_000_000
TICKER = "KXBTC15M-PREFLIGHT"


def test_fresh_quote_rejection_is_explicit_no_order_submitted(tmp_path):
    class NeverSend:
        def send_order(self, order):
            raise AssertionError("A pre-submit rejection must never POST")

    trader = ManualTrader(
        tmp_path,
        vault=SimpleNamespace(),
        client=NeverSend(),
        market_client=SimpleNamespace(),
    )
    calls = []

    def prepare(ticker, action, outcome, units, amount, *, fresh_account=False):
        calls.append(fresh_account)
        if fresh_account:
            raise TradingError("Price moved against you; quote again")
        return {
            "ticker": ticker, "action": action, "outcome": outcome,
            "side": "bid", "price": "0.5500", "unit_price": "0.5500",
            "count": "0.10", "fee_estimate": "0.0010",
            "amount_estimate": "0.0560",
        }

    trader._prepare = prepare
    preview = trader.preview(TICKER, "BUY", "UP", "DOLLARS", "0.10")
    with pytest.raises(OrderNotSubmitted, match="Price moved against you"):
        trader.place(preview["preview_id"], TICKER)
    assert calls == [False, True]


def test_auto_journals_precheck_failures_separately_from_unknown(tmp_path):
    now = [1_900_000_000 * NS]
    close = now[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {
        "market_ticker": TICKER, "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint, "final_forecast": None,
    }
    class Exchange:
        def __init__(self):
            self.place_calls = 0
        def preview(self, ticker, action, outcome, units, amount):
            return {
                "ticker": ticker, "action": action, "outcome": outcome,
                "preview_id": "fake-preview", "unit_price": "0.5500",
                "count": "0.10", "fee_estimate": "0.0010",
                "amount_estimate": "0.0560",
            }
        def place(self, preview_id, current_ticker):
            self.place_calls += 1
            raise OrderNotSubmitted("Price moved against you; quote again")

    manual = Exchange()
    engine = ArmedAutoTrader(
        manual, lambda: feed, now_ns=lambda: now[0],
        history_path=tmp_path / "journal.sqlite3",
    )
    engine.arm("0.10", continuous=True, start_worker=False)
    now[0] = checkpoint + NS
    feed["final_forecast"] = {
        "market_ticker": TICKER, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.90,
    }
    engine.tick()
    status = engine.status()
    assert status["last_result"] == "NOT_SUBMITTED"
    assert status["state"] == "WAITING"
    assert "Price moved against you" in status["message"]
    assert engine.history_store.recent_stages(TICKER)[0]["status"] == "NOT_SUBMITTED"
    assert manual.place_calls == 1
    for _ in range(3):
        engine.tick()
    assert manual.place_calls == 1
