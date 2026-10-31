"""Keep the executable order book as fresh as possible when preparing orders.

Account balance/position reads can be rate-limited; they must happen before
reading the final public quote so a forced delay cannot age the quote first.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kalshi_edge.trading.manual_trade import ManualTrader


def test_account_is_refreshed_before_final_market_quote_for_preview_and_execution():
    calls = []

    class Market:
        def quote(self, ticker):
            calls.append("book")
            assert ticker == "KXBTC15M-TEST"
            close = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
            return ({"market": {"ticker": ticker, "close_time": close, "status": "active"}},
                    {"orderbook_fp": {"yes_dollars": [["0.50", "20.00"]],
                                      "no_dollars": [["0.50", "20.00"]]}})

    trader = ManualTrader.__new__(ManualTrader)
    trader.market_client = Market()

    def account(ticker, *, fresh=False):
        calls.append("account_fresh" if fresh else "account_cached")
        return Decimal("3.00"), Decimal("0.00")

    trader._account = account

    for fresh in (False, True):
        calls.clear()
        plan = trader._prepare("KXBTC15M-TEST", "BUY", "UP", "DOLLARS",
                               "1.00", fresh_account=fresh)
        assert plan["ticker"] == "KXBTC15M-TEST"
        assert calls == (["account_fresh", "book"] if fresh
                         else ["account_cached", "book"])


def test_orderbook_is_observed_after_balance_refresh_changes_state():
    account_read = {"complete": False}

    class Market:
        def quote(self, ticker):
            assert account_read["complete"], "order book was read before account refresh"
            close = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
            return ({"market": {"ticker": ticker, "close_time": close, "status": "active"}},
                    {"orderbook_fp": {"yes_dollars": [["0.51", "10.00"]],
                                      "no_dollars": [["0.49", "10.00"]]}})

    trader = ManualTrader.__new__(ManualTrader)
    trader.market_client = Market()

    def account(ticker, *, fresh=False):
        account_read["complete"] = True
        return Decimal("3.00"), Decimal("0.00")

    trader._account = account
    quote = trader._prepare("KXBTC15M-TEST", "BUY", "UP", "DOLLARS",
                            "0.10", fresh_account=True)
    assert quote["price"] == "0.5100"
