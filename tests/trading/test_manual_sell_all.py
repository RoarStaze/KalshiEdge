"""Automatic position-based manual sell previews: simulated exchange only."""
from decimal import Decimal
from types import SimpleNamespace
import pytest

from kalshi_edge.trading.manual_trade import ManualTrader, TradingError, NoLiquidityError
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade


TICKER = "KXBTC15M-TEST"


class FakeAccount:
    def __init__(self, position="0.11", exposure="0.1020"):
        self.position = position
        self.exposure = exposure
        self.orders = []

    def get(self, path, *, params=None):
        if path == "/portfolio/balance":
            return {"balance_dollars": "3.16"}
        if path == "/portfolio/positions":
            return {"market_positions": [{
                "ticker": params["ticker"], "position_fp": self.position,
                "market_exposure_dollars": self.exposure,
            }]}
        raise AssertionError(path)

    def send_order(self, payload):
        self.orders.append(payload)
        return {"order_id":"test-sell-order","fill_count":"0"}

    def close(self):
        pass


class FakeMarket:
    def __init__(self, yes_qty="10.00", no_qty="10.00"):
        self.yes_qty = yes_qty
        self.no_qty = no_qty

    def quote(self, ticker):
        return ({"market": {"ticker":ticker, "status":"active",
                            "fractional_trading_enabled":True,
                            "close_time":"2099-01-01T00:00:00Z"}},
                {"orderbook_fp":{"yes_dollars":[["0.8700",self.yes_qty]],
                                 "no_dollars":[["0.8700",self.no_qty]]}})

    def close(self):
        pass


def make_trader(tmp_path, position="0.11", exposure="0.1020",
                yes_qty="10.00", no_qty="10.00"):
    account = FakeAccount(position, exposure)
    trader = ManualTrader(tmp_path,
                          vault=SimpleNamespace(exists=lambda:True),
                          client=account,
                          market_client=FakeMarket(yes_qty=yes_qty,no_qty=no_qty))
    return trader, account


def test_sell_market_shows_held_shares_and_average_entry(tmp_path):
    trader, _ = make_trader(tmp_path)
    market = trader.market(TICKER)
    assert market["position_yes"] == "0.1100"
    assert market["position_exposure_dollars"] == "0.1020"
    assert Decimal(market["average_entry_price"]) == (Decimal("0.1020") / Decimal("0.11")).quantize(Decimal("0.0001"))


@pytest.mark.parametrize("held,expected", [("0.11","UP"),("-0.18","DOWN")])
def test_sell_preview_auto_selects_entire_owned_side(tmp_path, held, expected):
    trader, account = make_trader(tmp_path, position=held)
    plan = trader.preview_all(TICKER)
    assert plan["action"] == "SELL"
    assert plan["outcome"] == expected
    assert plan["units"] == "SHARES"
    assert Decimal(plan["count"]) == abs(Decimal(held))
    assert plan["sell_all"] is True
    assert not account.orders


def test_sell_preview_rejects_missing_position(tmp_path):
    trader, account = make_trader(tmp_path, position="0.00")
    with pytest.raises(TradingError, match="No position"):
        trader.preview_all(TICKER)
    assert not account.orders


def test_sell_preview_rejects_insufficient_liquidity_for_full_exit(tmp_path):
    trader, account = make_trader(tmp_path, position="0.11", yes_qty="0.10")
    with pytest.raises(NoLiquidityError, match="entire position"):
        trader.preview_all(TICKER)
    assert not account.orders


def test_sell_order_rechecks_position_before_submit(tmp_path):
    trader, account = make_trader(tmp_path)
    preview = trader.preview_all(TICKER)
    account.position = "0.09"
    with pytest.raises(TradingError, match="Position.*changed|position.*changed"):
        trader.place(preview["preview_id"], TICKER)
    assert not account.orders


def test_sell_order_submitted_only_after_explicit_click(tmp_path):
    trader, account = make_trader(tmp_path)
    preview = trader.preview_all(TICKER)
    assert account.orders == []
    result = trader.place(preview["preview_id"], TICKER)
    assert result["action"] == "SELL"
    assert len(account.orders) == 1
    assert account.orders[0]["reduce_only"] is True
    assert Decimal(account.orders[0]["count"]) == Decimal("0.11")


def test_sell_ui_uses_readonly_holding_and_no_sell_amount_input():
    html = integrate_manual_trade(PAGE)
    assert 'id="manual-sell-shares"' in html
    assert 'id="manual-sell-holding"' in html
    assert 'id="manual-buy-inputbox"' in html
    assert 'id="manual-pills"' in html
    assert 'id="manual-entry-price"' in html
    assert '"sell-preview"' in html
    assert 'id="manual-sell-shares"' in html
    assert 'Sell with 1-Click' in html
