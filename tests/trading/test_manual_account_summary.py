"""Cash + portfolio header: authenticated balance values, no extra API reads.

Only fake portfolio and order-book transports are used; no real orders.
"""
from decimal import Decimal
from types import SimpleNamespace

from kalshi_edge.trading.manual_trade import ManualTrader
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.dashboard_ui import PAGE

TICKER = "KXBTC15M-TEST"


class Account:
    def __init__(self, balance):
        self.payload = balance
        self.balance_reads = 0
        self.positions_reads = 0

    def get(self, path, *, params=None):
        if path == "/portfolio/balance":
            self.balance_reads += 1
            return self.payload
        if path == "/portfolio/positions":
            self.positions_reads += 1
            return {"market_positions": []}
        raise AssertionError(path)

    def close(self):
        pass


class Market:
    def quote(self, ticker):
        return ({"market":{"ticker":ticker,"status":"active",
                           "close_time":"2099-01-01T00:00:00Z"}},
                {"orderbook_fp":{"yes_dollars":[["0.4000","5.00"]],
                                 "no_dollars":[["0.6000","5.00"]]}})

    def close(self):
        pass


def trader(tmp_path, balance):
    account = Account(balance)
    return ManualTrader(tmp_path, vault=SimpleNamespace(exists=lambda: True),
                        client=account, market_client=Market()), account


def test_portfolio_total_adds_cash_to_kalshi_marked_positions_in_cents(tmp_path):
    model, account = trader(tmp_path, {
        "balance": 308, "balance_dollars": "3.0800", "portfolio_value": 7,
    })
    view = model.market(TICKER)
    assert view["available_balance"] == "3.0800"
    assert view["portfolio_total"] == "3.1500"
    assert account.balance_reads == 1
    assert account.positions_reads == 1


def test_uses_fixed_point_dollar_cash_instead_of_rounded_cents(tmp_path):
    model, _ = trader(tmp_path, {
        "balance": 308, "balance_dollars": "3.0850", "portfolio_value": 7,
    })
    assert model.market(TICKER)["portfolio_total"] == "3.1550"


def test_portfolio_missing_does_not_fabricate_value(tmp_path):
    model, _ = trader(tmp_path, {"balance_dollars": "3.0800"})
    view = model.market(TICKER)
    assert view["available_balance"] == "3.0800"
    assert view["portfolio_total"] is None


def test_zero_position_value_still_displays_cash_total(tmp_path):
    model, _ = trader(tmp_path, {
        "balance_dollars": "3.0800", "portfolio_value": 0,
    })
    assert model.market(TICKER)["portfolio_total"] == "3.0800"


def test_cached_account_reused_for_preview_without_second_balance_read(tmp_path):
    model, account = trader(tmp_path, {
        "balance_dollars": "3.0800", "portfolio_value": 7,
    })
    model.market(TICKER)
    model.preview(TICKER, "BUY", "DOWN", "DOLLARS", "1.00")
    assert account.balance_reads == 1


def test_header_has_cash_and_portfolio_values_and_refresh_from_snapshot():
    page = integrate_manual_trade(PAGE)
    assert 'id="manual-cash"' in page
    assert 'id="manual-portfolio"' in page
    assert 'Cash</span>' in page
    assert 'Portfolio</span>' in page
    assert 'money(value.available_balance)' in page
    assert 'money(value.portfolio_total)' in page
    assert '.mt-account-summary' in page
    assert 'role="group" aria-label="Predictions account balances"' in page
