"""Signed realized P/L must be backed by a confirmed manual SELL fill."""
from decimal import Decimal
from types import SimpleNamespace

from kalshi_edge.trading.manual_trade import ManualTrader

TICKER = "KXBTC15M-TEST"


class Account:
    def __init__(self, fill="0.11"):
        self.position = "0.11"
        self.fill = fill
        self.orders = []

    def get(self, path, *, params=None):
        if path == "/portfolio/balance":
            return {"balance_dollars": "3.00", "balance": 300, "portfolio_value": 0}
        if path == "/portfolio/positions":
            return {"market_positions": (
                [{"ticker": TICKER, "position_fp": self.position,
                  "market_exposure_dollars": "0.1020"}]
                if Decimal(self.position) != 0 else []
            )}
        raise AssertionError(path)

    def send_order(self, payload):
        self.orders.append(payload)
        if Decimal(self.fill) > 0:
            self.position = str(Decimal(self.position) - Decimal(self.fill))
        return {"order_id": "confirmed-sell-1", "fill_count": self.fill,
                "average_fill_price": "0.8700", "average_fee_paid": "0.01"}

    def close(self):
        pass


class Market:
    def quote(self, ticker):
        assert ticker == TICKER
        return ({"market": {"ticker": ticker, "status": "active",
                           "close_time": "2099-01-01T00:00:00Z",
                           "fractional_trading_enabled": True}},
                {"orderbook_fp": {"yes_dollars": [["0.8700", "10.00"]],
                                  "no_dollars": [["0.8700", "10.00"]]}})

    def close(self):
        pass


def test_full_confirmed_sell_surfaces_realized_loss_and_persists_across_market_reads(tmp_path):
    account = Account()
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(exists=lambda: True),
                          client=account, market_client=Market())
    preview = trader.preview_all(TICKER)
    assert trader.preview_action(preview["preview_id"]) == "SELL"
    trade = trader.place(preview["preview_id"], TICKER)
    assert trade["status"] == "FILLED"
    assert trade["action"] == "SELL"
    assert trade["realized_pnl_dollars"] == "-0.0074"
    fresh = trader.market(TICKER)
    assert fresh["position_yes"] == "0.0000"
    assert fresh["last_sell"]["ticker"] == TICKER
    assert fresh["last_sell"]["realized_pnl_dollars"] == "-0.0074"
    assert fresh["last_sell"]["status"] == "SOLD"


def test_no_fill_never_claims_sold_or_realized_profit(tmp_path):
    account = Account(fill="0")
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(exists=lambda: True),
                          client=account, market_client=Market())
    preview = trader.preview_all(TICKER)
    result = trader.place(preview["preview_id"], TICKER)
    assert result["status"] == "NO_FILL"
    assert result.get("realized_pnl_dollars") is None
    assert trader.market(TICKER)["last_sell"] is None


def test_partial_sell_never_claims_full_exit(tmp_path):
    account = Account(fill="0.05")
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(exists=lambda: True),
                          client=account, market_client=Market())
    preview = trader.preview_all(TICKER)
    result = trader.place(preview["preview_id"], TICKER)
    assert result["status"] == "PARTIAL"
    assert trader.market(TICKER)["last_sell"] is None
    assert trader.market(TICKER)["held_side"] == "UP"
