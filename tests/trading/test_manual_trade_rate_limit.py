"""Regression checks for Kalshi's dedicated balance-read rate limit.

All exchange requests are simulated; tests must not place real trades.
"""
from datetime import datetime
from decimal import Decimal

import pytest

import kalshi_edge.trading.manual_trade as manual_trade
from kalshi_edge.trading.manual_trade import ManualTrader, TradingError


TICKER = "KXBTC15M-26OCT310330-00"
NEXT_TICKER = "KXBTC15M-26OCT310345-00"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.balance = "3.16"
        self.submitted = []

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        if path == "/portfolio/balance":
            return {"balance_dollars": self.balance}
        if path == "/portfolio/positions":
            return {"market_positions": [
                {"ticker": params["ticker"], "position_fp": "0"}
            ]}
        raise AssertionError(f"Unexpected account GET: {path}")

    def send_order(self, payload):
        self.submitted.append(payload)
        return {"order_id": "mock", "fill_count": "0"}

    def close(self):
        pass


class FakeMarket:
    def quote(self, ticker):
        return (
            {"market": {"ticker": ticker, "status": "active",
                        "close_time": "2099-01-01T00:00:00Z",
                        "fractional_trading_enabled": True}},
            {"orderbook_fp": {"yes_dollars": [["0.30", "100"]],
                              "no_dollars": [["0.30", "100"]]}},
        )

    def close(self):
        pass


@pytest.fixture
def session(monkeypatch, tmp_path):
    clock = [100.0]
    sleeps = []

    def fake_sleep(seconds):
        assert seconds >= 0
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(manual_trade.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(manual_trade.time, "sleep", fake_sleep)
    client = FakeClient()
    trader = ManualTrader(tmp_path, client=client, market_client=FakeMarket())
    return clock, sleeps, client, trader


def balance_reads(client):
    return sum(path == "/portfolio/balance" for path, _ in client.calls)


def test_market_then_preview_reuses_account_snapshot(session):
    _, _, client, trader = session
    assert trader.market(TICKER)["available_balance"] == "3.1600"
    assert trader.preview(TICKER, "BUY", "DOWN", "DOLLARS", "1.00")["preview_id"]
    assert balance_reads(client) == 1


def test_balance_refreshes_after_cache_window(session):
    clock, _, client, trader = session
    assert trader._account(TICKER)[0] == Decimal("3.16")
    clock[0] += 2
    client.balance = "2.50"
    assert trader._account(TICKER)[0] == Decimal("2.50")
    assert balance_reads(client) == 2


def test_ticker_change_does_not_burst_balance_requests(session):
    _, sleeps, client, trader = session
    trader._account(TICKER)
    trader._account(NEXT_TICKER)
    assert balance_reads(client) == 2
    assert sum(sleeps) >= 1.0


def test_live_order_rechecks_balance_even_with_recent_preview(session):
    _, sleeps, client, trader = session
    preview = trader.preview(TICKER, "BUY", "DOWN", "DOLLARS", "1.00")
    client.balance = "0.01"
    with pytest.raises(TradingError, match="(Insufficient cash|smaller than one executable)"):
        trader.place(preview["preview_id"], TICKER)
    assert not client.submitted
    assert balance_reads(client) == 2
    assert sum(sleeps) >= 1.0
