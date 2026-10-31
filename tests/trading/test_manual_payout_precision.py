"""Regression: fee-aware decimal-price sizing and truthful payout display.

Only pure market fixtures are used; no real order posts occur.
"""
from decimal import Decimal

from kalshi_edge.trading.manual_trade import ManualTrader
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade


def book(yes_bid="0.9920", available="500.00"):
    return {"orderbook_fp": {
        "yes_dollars": [[yes_bid, available]],
        "no_dollars": [["0.0010", "500.00"]],
    }}


def market(fractional=True):
    return {"market": {"status": "active", "fractional_trading_enabled": fractional}}


def plan(*, ask="0.0080", available="500.00", fractional=True, amount="1.00"):
    # The YES bid is the inverse of the DOWN ask.
    yes_bid = Decimal("1") - Decimal(ask)
    return ManualTrader.size_order(
        None, Decimal("3.16"), "BUY", "DOWN", "DOLLARS",
        Decimal(amount), Decimal("0"),
        book(f"{yes_bid:.4f}", available), market(fractional),
    )


def test_subcent_buy_uses_cent_aligned_exchange_fee_estimate():
    result = plan(ask="0.0080")
    assert result["unit_price"] == "0.0080"
    assert result["count"] == "116.88"
    assert result["maximum_payout"] == "116.8800"
    assert result["fee_estimate"] == "0.064960"
    assert result["amount_estimate"] == "1.0000"
    assert Decimal(result["count"]) * Decimal(result["unit_price"]) + Decimal(result["fee_estimate"]) <= Decimal("1.00")


def test_one_cent_budget_can_buy_minimum_fractional_contract():
    result = ManualTrader.size_order(
        None, Decimal("0.01"), "BUY", "UP", "DOLLARS",
        Decimal("0.01"), Decimal("0"),
        book(yes_bid="0.1000"), market(True),
    )
    assert result["count"] == "0.01"
    assert result["amount_estimate"] == "0.0100"


def test_sizing_respects_actual_bid_depth():
    result = plan(ask="0.0080", available="41.25")
    assert result["count"] == "41.25"
    assert result["maximum_payout"] == "41.2500"


def test_nonfractional_market_does_not_invent_fractional_fills():
    result = plan(ask="0.0080", fractional=False)
    assert result["count"] == "116.00"
    assert result["maximum_payout"] == "116.0000"


def test_different_subcent_price_changes_payout():
    lower = plan(ask="0.0080")
    higher = plan(ask="0.0090")
    assert Decimal(lower["maximum_payout"]) > Decimal(higher["maximum_payout"])
    assert Decimal(higher["maximum_payout"]) > Decimal("102.00")


def test_dashboard_shows_subcent_prices_and_precise_odds_not_rounded_whole_cents():
    page = integrate_manual_trade(PAGE)
    assert "priceCents(value.up_ask)" in page
    assert "priceCents(value.down_ask)" in page
    assert "probabilityPercent(value.unit_price)" in page
    assert "Math.round(Number(value.down_ask)*100)" not in page
    assert "Math.round(Number(value.unit_price)*100)" not in page
    assert 'money(value.maximum_payout)' in page
