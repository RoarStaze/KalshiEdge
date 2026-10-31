from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kalshi_edge.trading.risk import (
    EntryRejected, best_ask, plan_locked_entry, taker_fee_reserve,
)

NS = 1_000_000_000
SCHEDULED = 1_800_000_000_000_000_000
TICKER = "KXBTC15M-TEST"


def fixture_data(*, outcome="ABOVE", pwin=0.97, ask=0.50):
    forecast = SimpleNamespace(
        market_ticker=TICKER, forecast=outcome, confidence=0.94,
        directional_probability=pwin, strike=60000,
        scheduled_ts_ns=SCHEDULED, prediction_ts_ns=SCHEDULED,
    )
    close = datetime.fromtimestamp(
        (SCHEDULED + 240 * NS) / NS, timezone.utc
    ).isoformat()
    market = {"market": {
        "ticker": TICKER, "status": "active",
        "fractional_trading_enabled": True, "floor_strike": 60000,
        "close_time": close,
    }}
    other_bid = 1 - ask
    if outcome == "ABOVE":
        book = {"orderbook_fp": {
            "no_dollars": [[f"{other_bid:.4f}", "10.00"]],
            "yes_dollars": [["0.2000", "10.00"]],
        }}
    else:
        book = {"orderbook_fp": {
            "yes_dollars": [[f"{other_bid:.4f}", "10.00"]],
            "no_dollars": [["0.2000", "10.00"]],
        }}
    return forecast, market, book


def test_ask_uses_opposite_book_highest_bid():
    book = {"orderbook_fp": {
        "yes_dollars": [["0.31", "1.00"], ["0.65", "3.00"]],
        "no_dollars": [["0.72", "2.00"], ["0.22", "5.00"]],
    }}
    assert best_ask(book, "ABOVE") == (Decimal("0.28"), Decimal("2.00"))
    assert best_ask(book, "BELOW") == (Decimal("0.35"), Decimal("3.00"))


@pytest.mark.parametrize("side", ["ABOVE", "BELOW"])
def test_all_in_cap_and_positive_net_edge(side):
    f, market, book = fixture_data(outcome=side)
    plan = plan_locked_entry(f, market, book, SCHEDULED + NS)
    assert plan.outcome == side
    assert Decimal(plan.worst_case_debit) <= Decimal("0.10")
    assert Decimal(plan.estimated_net_profit) >= Decimal("0.005")
    assert Decimal(plan.count) % Decimal("0.01") == 0
    assert Decimal(plan.price) == Decimal("0.5")


def test_stop_on_model_skip():
    f, market, book = fixture_data()
    f.forecast = "SKIP"
    with pytest.raises(EntryRejected, match="SKIP"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)


def test_refuses_late_trade_even_if_forecast_exists():
    f, market, book = fixture_data()
    with pytest.raises(EntryRejected, match="20-second"):
        plan_locked_entry(f, market, book, SCHEDULED + 21 * NS)


def test_refuses_wrong_market_or_strike_or_close():
    f, market, book = fixture_data()
    market["market"]["ticker"] = "OTHER"
    with pytest.raises(EntryRejected, match="market mismatch"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)
    market["market"]["ticker"] = TICKER
    market["market"]["floor_strike"] = 20000
    with pytest.raises(EntryRejected, match="strike mismatch"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)


def test_refuses_no_liquidity_and_non_fractional_markets():
    f, market, book = fixture_data()
    market["market"]["fractional_trading_enabled"] = False
    with pytest.raises(EntryRejected, match="fractional"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)
    market["market"]["fractional_trading_enabled"] = True
    with pytest.raises(EntryRejected, match="liquidity"):
        plan_locked_entry(f, market, {"orderbook_fp": {"no_dollars": []}}, SCHEDULED + NS)


def test_no_trade_when_net_edge_is_negative_after_fee():
    f, market, book = fixture_data(pwin=0.86, ask=0.85)
    with pytest.raises(EntryRejected, match="fee-aware"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)


def test_taker_fee_reserve_includes_rounding_buffer():
    assert taker_fee_reserve(Decimal("0.5"), Decimal("0.1")) >= Decimal("0.01")

def test_market_without_undocumented_fractional_field_still_allows_paper_model():
    """GET /markets/{ticker} does not guarantee fractional_trading_enabled."""
    f, market, book = fixture_data()
    del market["market"]["fractional_trading_enabled"]
    plan = plan_locked_entry(f, market, book, SCHEDULED + NS)
    assert plan.outcome == "ABOVE"
    assert Decimal(plan.worst_case_debit) <= Decimal("0.10")
    assert Decimal(plan.count) % Decimal("0.01") == 0


@pytest.mark.parametrize("unsupported", [False, None, "false", 0])
def test_explicit_non_true_fractional_flag_still_fails_closed(unsupported):
    f, market, book = fixture_data()
    market["market"]["fractional_trading_enabled"] = unsupported
    with pytest.raises(EntryRejected, match="fractional"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)


def test_missing_flag_does_not_allow_finalized_market():
    f, market, book = fixture_data()
    del market["market"]["fractional_trading_enabled"]
    market["market"]["status"] = "finalized"
    with pytest.raises(EntryRejected, match="market not open"):
        plan_locked_entry(f, market, book, SCHEDULED + NS)

def test_cost_skip_reports_best_candidate_and_two_fee_models():
    """One priced SKIP must explain why the quoted price cannot pass."""
    f, market, book = fixture_data(pwin=0.95, ask=0.80)
    with pytest.raises(EntryRejected, match="fee-aware") as caught:
        plan_locked_entry(f, market, book, SCHEDULED + NS)
    detail = caught.value.diagnostics
    assert detail["model_win_probability"] == "0.950000"
    assert detail["entry_price"] == "0.8000"
    assert detail["candidate_quantity"] == "0.10"
    assert detail["fee_reserve"] == "0.020000"
    assert detail["all_in_debit"] == "0.100000"
    assert detail["expected_net_profit"] == "-0.005000"
    assert detail["reference_fee_non_direct"] == "0.010000"
    assert detail["fractional_execution_verified"] is False


def test_missing_gross_edge_still_has_quote_diagnostics():
    f, market, book = fixture_data(pwin=0.86, ask=0.90)
    with pytest.raises(EntryRejected, match="no gross model edge") as caught:
        plan_locked_entry(f, market, book, SCHEDULED + NS)
    assert caught.value.diagnostics["entry_price"] == "0.9000"
    assert caught.value.diagnostics["model_win_probability"] == "0.860000"


def test_reference_fee_uses_six_decimal_model_and_cent_aligned_balance():
    from kalshi_edge.trading.risk import reference_nondirect_fee
    assert reference_nondirect_fee(Decimal("0.8"), Decimal("0.1")) == Decimal("0.010000")
    assert reference_nondirect_fee(Decimal("0.5"), Decimal("0.1")) == Decimal("0.010000")
