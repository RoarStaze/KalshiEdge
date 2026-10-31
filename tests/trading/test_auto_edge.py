"""Fee-aware value gate for real-money AutoTrade.
Tests use fake local quotes only; never submit real orders.
"""
from decimal import Decimal

import pytest

from kalshi_edge.trading.auto_edge import assess_auto_buy_edge


def quote(price="0.4000", count="0.25", fee="0.0060", total="0.1060"):
    return {"unit_price": price, "count": count,
            "fee_estimate": fee, "amount_estimate": total}


def test_expensive_contract_skips_even_with_90pct_confidence():
    decision = assess_auto_buy_edge(quote("0.9800", "0.11", "0.0050", "0.1128"), 0.90)
    assert decision["approved"] is False
    assert decision["reason"] == "NON_POSITIVE_EDGE"
    assert Decimal(decision["conservative_win_probability"]) == Decimal("0.92")


def test_net_edge_uses_win_probability_and_entire_fee_inclusive_debit():
    decision = assess_auto_buy_edge(quote(), 0.90)
    assert decision["approved"] is True
    # (0.95 - 0.03) * 0.25 - 0.106 = +0.124
    assert Decimal(decision["expected_net_dollars"]) == Decimal("0.124000")


def test_near_zero_edge_rejected_by_minimum_margin():
    # p win = 0.82; debit = 0.080, qty = 0.10 => net = 0.002 < 0.005
    decision = assess_auto_buy_edge(quote("0.7800", "0.10", "0.0020", "0.0800"), 0.70)
    assert decision["approved"] is False
    assert decision["reason"] == "INSUFFICIENT_EDGE"


@pytest.mark.parametrize("field,value",[
    ("unit_price", None), ("count", None), ("fee_estimate", None),
    ("amount_estimate", None), ("unit_price", "NaN"), ("count", "Infinity"),
    ("fee_estimate", "-0.0001"), ("count", "0"), ("unit_price", "1.01"),
    ("amount_estimate", "0"), ("unit_price", True)])
def test_missing_or_bad_quote_never_approves_trade(field, value):
    data = quote()
    data[field] = value
    decision = assess_auto_buy_edge(data, 0.90)
    assert decision["approved"] is False
    assert decision["reason"] == "INVALID_QUOTE"


@pytest.mark.parametrize("confidence", [None, -0.01, 1.01, float("nan"), True])
def test_invalid_confidence_fails_closed(confidence):
    assert assess_auto_buy_edge(quote(), confidence)["approved"] is False


def test_previews_cannot_understate_price_plus_fees():
    data = quote("0.5000", "1.00", "0.0400", "0.0100")
    assert assess_auto_buy_edge(data, 0.95)["reason"] == "INVALID_QUOTE"


def test_require_significant_edge_relative_to_spend():
    # 2% of debit is more demanding than half a cent for large orders.
    decision = assess_auto_buy_edge(quote("0.8500", "10.00", "0.0500", "8.5500"), 0.80)
    assert decision["approved"] is False
    assert Decimal(decision["minimum_required_dollars"]) == Decimal("0.171000")


def test_no_edge_is_checked_before_exchange_submit_in_armed_auto(tmp_path):
    from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
    NS = 1_000_000_000
    now = 1_900_000_000 * NS
    close = now + 400 * NS
    checkpoint = close - 240 * NS
    ticker = "KXBTC15M-TEST"
    state = {
        "market_ticker": ticker, "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint, "status": "LIVE",
        "final_forecast": None, "latest_prediction": None,
    }

    class Manual:
        orders = []
        previews = []
        def preview(self, ticker, action, outcome, units, amount):
            self.previews.append((ticker, action, outcome, amount))
            return {"ticker": ticker, "action": action, "outcome": outcome,
                    "preview_id": "unsafe", **quote("0.9900", "0.11", "0.0030", "0.1119")}
        def place(self, preview_id, ticker):
            self.orders.append((preview_id, ticker))
            raise AssertionError("Unprofitable order must never be submitted")

    manual = Manual()
    engine = ArmedAutoTrader(manual, lambda: state, now_ns=lambda: now,
                             history_path=tmp_path / "history.sqlite3")
    engine.arm("0.10", start_worker=False)
    state["final_forecast"] = {
        "market_ticker": ticker, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.90,
    }
    now = checkpoint
    engine.tick()
    assert manual.previews and not manual.orders
    assert engine.status()["last_result"] == "SKIPPED"
    assert engine.history()["entries"] == []


def test_reversal_sells_are_not_blocked_by_new_buy_value_gate():
    from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
    # The value gate applies to buys, not risk-reducing sells.
    import inspect
    source = inspect.getsource(ArmedAutoTrader._order)
    assert 'if action == "BUY":' in source
