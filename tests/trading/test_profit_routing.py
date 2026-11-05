"""Maker/taker opportunity choice is exercised without Kalshi credentials."""
from decimal import Decimal

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS = 1_000_000_000


def _run(tmp_path, *, maker_exists: bool):
    ticker = "KXBTC15M-PROFIT-ROUTE"
    now = [1_900_000_000 * NS]
    close = now[0] + 900 * NS
    checkpoint = close - 480 * NS
    live = {
        "market_ticker": ticker,
        "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint,
        "final_forecast": None,
    }

    class OfflineExchange:
        def __init__(self):
            self.maker_previews = 0
            self.placed = []
        def preview(self, ticker, action, side, units, amount):
            price, qty = Decimal("0.70"), Decimal("0.10")
            return {
                "ticker": ticker, "action": action, "outcome": side,
                "units": units, "preview_id": "q-1",
                "unit_price": str(price), "count": str(qty),
                "fee_estimate": "0.002", "amount_estimate": str(price * qty + Decimal(".002")),
                "available_balance": "100.00", "input_amount": amount,
            }
        def preview_auto_maker(self, preview, confidence):
            self.maker_previews += 1
            if not maker_exists:
                return None
            return {
                "unit_price": "0.60", "count": "0.10",
                "fee_estimate": "0.002", "amount_estimate": "0.062",
                "edge_diagnostics": {
                    "approved": True, "reason": "APPROVED",
                    "model_win_probability": "0.950000",
                    "expected_net_dollars": "0.030000",
                },
            }
        def place_auto_value(self, preview_id, ticker, *, confidence, prefer_maker):
            self.placed.append(prefer_maker)
            return {"status": "FILLED", "fill_count": "0.10", "order_id": "offline"}

    exchange = OfflineExchange()
    bot = ArmedAutoTrader(exchange, lambda: live, now_ns=lambda: now[0],
                         history_path=tmp_path / "data" / "autotrade.sqlite3")
    bot.arm("0.10", continuous=True, start_worker=False)
    now[0] = checkpoint + NS
    live["final_forecast"] = {
        "market_ticker": ticker, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": .90,
    }
    bot.tick()
    return exchange, bot.status()


def test_approved_maker_is_considered_even_when_taker_has_positive_ev(tmp_path):
    exchange, status = _run(tmp_path, maker_exists=True)
    assert exchange.maker_previews == 1
    assert exchange.placed == [True]
    assert status["latest_opportunity"]["route"] == "MAKER_FIRST"
    assert Decimal(status["latest_opportunity"]["expected_net_dollars"]) > 0


def test_approved_taker_directly_routes_ioc_when_no_maker_edge(tmp_path):
    exchange, status = _run(tmp_path, maker_exists=False)
    assert exchange.maker_previews == 1
    assert exchange.placed == [False]
    assert status["latest_opportunity"]["route"] == "TAKER_IOC"
    assert Decimal(status["latest_opportunity"]["expected_net_dollars"]) > 0
