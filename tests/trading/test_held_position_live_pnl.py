"""Causal, fee-aware position P/L for the visible Trade-card status.

No exchange orders are submitted; missing bid depth or cost basis cannot be
turned into a made-up positive return.
"""
from decimal import Decimal

from kalshi_edge.trading.manual_trade import estimate_held_position_pnl


def _book(yes_bid="0.6000", no_bid="0.3000", yes_qty="10.00", no_qty="10.00"):
    return {"orderbook_fp": {
        "yes_dollars": [[yes_bid, yes_qty]],
        "no_dollars": [[no_bid, no_qty]],
    }}


def test_up_holding_uses_yes_bid_and_estimated_exit_fee():
    result = estimate_held_position_pnl(
        _book(), position=Decimal("2.00"), exposure=Decimal("1.00"),
    )
    assert result["held_side"] == "UP"
    assert result["pnl_status"] == "ESTIMATED"
    assert result["unrealized_pnl_dollars"] == "0.1600"


def test_down_holding_uses_no_bid_and_correct_negative_sign():
    result = estimate_held_position_pnl(
        _book(), position=Decimal("-2.00"), exposure=Decimal("1.00"),
    )
    assert result["held_side"] == "DOWN"
    assert result["pnl_status"] == "ESTIMATED"
    assert result["unrealized_pnl_dollars"] == "-0.4300"


def test_no_holding_is_flat_not_an_invented_buy():
    result = estimate_held_position_pnl(
        _book(), position=Decimal("0"), exposure=Decimal("0"),
    )
    assert result["held_side"] is None
    assert result["unrealized_pnl_dollars"] is None
    assert result["pnl_status"] == "NO_POSITION"


def test_missing_cost_basis_fails_closed():
    result = estimate_held_position_pnl(
        _book(), position=Decimal("1"), exposure=None,
    )
    assert result["held_side"] == "UP"
    assert result["unrealized_pnl_dollars"] is None
    assert result["pnl_status"] == "COST_BASIS_UNAVAILABLE"


def test_insufficient_full_position_bid_liquidity_fails_closed():
    result = estimate_held_position_pnl(
        _book(yes_qty="0.50"), position=Decimal("2"), exposure=Decimal("1"),
    )
    assert result["held_side"] == "UP"
    assert result["unrealized_pnl_dollars"] is None
    assert result["pnl_status"] == "INSUFFICIENT_LIQUIDITY"


def test_authenticated_market_snapshot_projects_held_side_and_live_pnl(tmp_path):
    from kalshi_edge.trading.manual_trade import ManualTrader

    class FakeAccount:
        def get(self, path, params=None):
            if path == "/portfolio/balance":
                return {"balance_dollars": "3.0000", "balance": 300,
                        "portfolio_value": 150}
            if path == "/portfolio/positions":
                return {"market_positions": [{
                    "ticker": "KXBTC15M-TEST", "position_fp": "2.00",
                    "market_exposure_dollars": "1.00",
                }]}
            raise AssertionError("Unexpected read: " + path)

    class FakeMarket:
        def quote(self, ticker):
            assert ticker == "KXBTC15M-TEST"
            return {"market": {"ticker": ticker, "status": "active"}}, _book()

    trader = ManualTrader(tmp_path, client=FakeAccount(), market_client=FakeMarket())
    result = trader.market("KXBTC15M-TEST")
    assert result["position_yes"] == "2.0000"
    assert result["held_side"] == "UP"
    assert result["pnl_status"] == "ESTIMATED"
    assert result["unrealized_pnl_dollars"] == "0.1600"
