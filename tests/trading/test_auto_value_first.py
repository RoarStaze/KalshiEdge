"""Value-first AutoTrade: all tests are offline and place no real orders."""
from decimal import Decimal

from kalshi_edge.trading.auto_edge import assess_auto_buy_edge, suggest_auto_stake
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS = 1_000_000_000


def _preview(price="0.8000", count="0.12", fee="0.0050", balance="3.54"):
    return {
        "ticker": "KXBTC15M-VALUE-CHECK", "action": "BUY",
        "outcome": "UP", "preview_id": "quote-1",
        "unit_price": price, "count": count, "fee_estimate": fee,
        "amount_estimate": format(Decimal(price) * Decimal(count) + Decimal(fee), ".4f"),
        "available_balance": balance,
    }


def test_risk_size_caps_oversized_user_request_at_five_percent():
    decision = suggest_auto_stake(_preview(), 0.98, "0.50")
    assert decision == Decimal("0.17")


def test_risk_size_refuses_unprofitable_and_unverifiable_entries():
    assert suggest_auto_stake(_preview(), 0.69, "0.50") is None
    p = _preview()
    del p["available_balance"]
    assert suggest_auto_stake(p, 0.98, "0.50") is None


def test_required_edge_is_at_least_three_cents_per_contract():
    p = _preview(price="0.8200", count="1.00", fee="0.0000")
    decision = assess_auto_buy_edge(p, 0.74)
    assert Decimal(decision["minimum_required_dollars"]) >= Decimal("0.030000")


def test_real_execution_path_rejects_expensive_final_before_journal(tmp_path):
    ticker = "KXBTC15M-VALUE-CHECK"
    now = [1_900_000_000 * NS]
    close = now[0] + 900 * NS
    checkpoint = close - 480 * NS
    state = {
        "market_ticker": ticker, "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint, "final_forecast": None,
    }

    class Exchange:
        def __init__(self):
            self.orders = []
        def preview(self, ticker, action, side, units, amount):
            return {**_preview(price="0.9900", count="0.10", fee="0.0010"),
                    "ticker": ticker, "outcome": side}
        def place_auto_value(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "FILLED", "fill_count": "0.10", "order_id": "bad"}

    exchange = Exchange()
    bot = ArmedAutoTrader(
        exchange, lambda: state, now_ns=lambda: now[0],
        history_path=tmp_path / "journal.sqlite3",
    )
    bot.arm("0.10", continuous=True, start_worker=False)
    now[0] = checkpoint + NS
    state["final_forecast"] = {
        "market_ticker": ticker, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.90,
    }
    bot.tick()
    assert exchange.orders == []
    assert not bot.history_store.has_attempt(ticker)
    rejections = bot.history_store.recent_quote_rejections()
    assert rejections and rejections[0]["reason"] == "NON_POSITIVE_EDGE"
