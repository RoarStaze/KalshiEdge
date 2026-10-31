"""A partial verified scorecard must never be described as lifetime returns."""

from kalshi_edge.trading.autotrade_scorecard import summarize_autotrade_performance


def market(ticker, pnl=None, *, status="FILLED", pnl_status="SETTLED"):
    return {
        "ticker": ticker, "status": status,
        "pnl_status": pnl_status,
        "realized_pnl_dollars": pnl,
    }


def test_signed_fee_inclusive_totals_are_partial_when_any_fill_unverified():
    entries = [
        market("one", "0.0197"),
        market("two", "-0.10"),
        market("three", "0.0230"),
        market("four", None, pnl_status="PENDING"),
        market("five", None, status="NO_FILL", pnl_status="UNAVAILABLE"),
    ]
    result = summarize_autotrade_performance(entries)
    assert result["history_market_count"] == 5
    assert result["verified_settled_markets"] == 3
    assert result["unverified_filled_markets"] == 1
    assert result["no_fill_markets"] == 1
    assert result["gains"] == 2
    assert result["losses"] == 1
    assert result["net_pnl_dollars"] == "-0.0573"
    assert result["gross_gains_dollars"] == "0.0427"
    assert result["gross_losses_dollars"] == "0.10"
    assert result["is_complete_for_filled_markets"] is False
    assert result["scope"] == "RECENT_AUTOTRADE_JOURNAL_ONLY"


def test_no_settled_markets_does_not_claim_zero_profit():
    result = summarize_autotrade_performance([
        market("one", None, pnl_status="PENDING"),
        market("two", None, status="UNKNOWN", pnl_status="UNAVAILABLE"),
    ])
    assert result["net_pnl_dollars"] is None
    assert result["verified_settled_markets"] == 0
    assert result["unverified_filled_markets"] == 1
    assert result["other_unverified_markets"] == 1


def test_exact_zero_is_distinct_from_no_verifiable_profit():
    result = summarize_autotrade_performance([market("one", "0.0000")])
    assert result["net_pnl_dollars"] == "0.00"
    assert result["breakeven"] == 1
    assert result["is_complete_for_filled_markets"] is True


def test_reject_invalid_or_non_finite_exchange_numbers_from_score():
    result = summarize_autotrade_performance([
        market("a", "NaN"), market("b", "Infinity"),
        market("c", 1.0), market("d", True),
    ])
    assert result["net_pnl_dollars"] is None
    assert result["unverified_filled_markets"] == 4
    assert result["verified_settled_markets"] == 0


def test_duplicate_tickers_not_double_counted():
    result = summarize_autotrade_performance([
        market("same", "0.10"), market("same", "0.10"),
        market("other", "-0.03"),
    ])
    assert result["duplicate_records_excluded"] == 2
    assert result["verified_settled_markets"] == 1
    assert result["net_pnl_dollars"] == "-0.03"
    assert result["is_complete_for_filled_markets"] is False


def test_unsettled_numeric_value_is_not_included_even_when_present():
    result = summarize_autotrade_performance([
        market("one", "999.00", pnl_status="PENDING"),
        market("two", "0.01", status="NO_FILL"),
    ])
    assert result["net_pnl_dollars"] is None
    assert result["verified_settled_markets"] == 0


def test_history_route_exposes_scorecard_without_mutating_accounts(tmp_path):
    from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
    from kalshi_edge.trading.autotrade_history import AutoTradeHistory

    journal = tmp_path / "autotrade-history.sqlite3"
    store = AutoTradeHistory(journal)
    store.record_attempt(
        ticker="KXBTC15M-TEST", attempted_at_ns=1000,
        outcome="UP", amount="0.10", confidence=.80)
    store.record_result(ticker="KXBTC15M-TEST", status="UNKNOWN")
    trader = ArmedAutoTrader(object(), lambda: {}, history_path=journal)
    response = trader.history()
    assert response["summary"]["net_pnl_dollars"] is None
    assert response["summary"]["history_market_count"] == 1
    assert response["summary"]["other_unverified_markets"] == 1


def test_buy_auto_ui_renders_only_verified_totals_and_completeness():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    html = integrate_manual_trade(PAGE)
    assert 'id="manual-auto-performance"' in html
    assert 'result.summary.net_pnl_dollars' in html
    assert 'result.summary.is_complete_for_filled_markets' in html
    assert 'Verified net P/L' in html
    assert 'recent markets' in html
