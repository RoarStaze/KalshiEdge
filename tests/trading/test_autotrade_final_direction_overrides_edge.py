"""Regression tests: the final directional forecast, not estimated model edge, authorizes AutoTrade.

All exchange operations use a fake. No actual trades are submitted.
"""
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS = 1_000_000_000


class Exchange:
    def __init__(self, *, invalid_quote=False):
        self.invalid_quote = invalid_quote
        self.orders = []
        self.previews = []

    def preview(self, ticker, action, outcome, units, amount):
        self.previews.append((ticker, action, outcome, units, amount))
        return {
            "ticker": ticker, "action": action, "outcome": outcome,
            "preview_id": "final-quote",
            "count": "0.10", "unit_price": "0.9900",
            "fee_estimate": "0.0010",
            "amount_estimate": "0.0010" if self.invalid_quote else "0.1000",
        }

    def place(self, preview_id, ticker):
        self.orders.append((preview_id, ticker))
        return {"status": "FILLED", "fill_count": "0.10", "order_id": "fake-order"}


def setup_trader(tmp_path, invalid_quote=False):
    clock = [1_900_000_000 * NS]
    ticker = "KXBTC15M-FINAL-EDGE-REGRESSION"
    close = clock[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {
        "market_ticker": ticker,
        "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint,
        "final_forecast": None,
    }
    exchange = Exchange(invalid_quote=invalid_quote)
    trader = ArmedAutoTrader(
        exchange, lambda: feed, now_ns=lambda: clock[0],
        history_path=tmp_path / "journal.sqlite3",
    )
    trader.arm("0.10", continuous=True, start_worker=False)
    clock[0] = checkpoint + NS
    feed["final_forecast"] = {
        "market_ticker": ticker, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.90,
    }
    return exchange, trader


def test_valid_high_price_quote_executes_locked_final_even_with_negative_model_edge(tmp_path):
    exchange, trader = setup_trader(tmp_path)
    trader.tick()
    assert len(exchange.previews) == 1
    assert len(exchange.orders) == 1
    assert trader.status()["last_result"] == "FILLED"
    assert trader.status()["state"] == "WAITING"
    assert trader.history_store.recent_stages("KXBTC15M-FINAL-EDGE-REGRESSION")[0]["status"] == "FILLED"
    for _ in range(3):
        trader.tick()
    assert len(exchange.orders) == 1


def test_invalid_fee_quote_is_never_submitted_even_when_final_direction_exists(tmp_path):
    exchange, trader = setup_trader(tmp_path, invalid_quote=True)
    trader.tick()
    assert len(exchange.previews) == 1
    assert exchange.orders == []
    assert trader.status()["state"] == "WAITING"


def test_real_manual_trader_executes_final_up_despite_negative_model_edge(tmp_path):
    """Final-only strategy must use a fresh budgeted IOC, not the EV-gated path."""
    from decimal import Decimal
    from types import SimpleNamespace
    from kalshi_edge.trading.manual_trade import ManualTrader

    class Client:
        def __init__(self):
            self.orders = []
        def send_order(self, payload):
            self.orders.append(dict(payload))
            return {"order_id": "offline-final-order", "fill_count": payload["count"]}

    ticker = "KXBTC15M-FINAL-REAL-PATH"
    clock = [1_900_000_000 * NS]
    close = clock[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {
        "market_ticker": ticker, "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint, "final_forecast": None,
    }
    client = Client()
    manual = ManualTrader(
        tmp_path, vault=SimpleNamespace(), client=client,
        market_client=SimpleNamespace(),
    )
    quote = {
        "ticker": ticker, "action": "BUY", "outcome": "UP",
        "units": "DOLLARS", "side": "bid", "price": "0.8800",
        "unit_price": "0.8800", "count": "1.17",
        "fee_estimate": "0.010000", "amount_estimate": "1.0396",
        "available_balance": "35.18", "position_yes": "0.00",
    }
    manual._prepare = lambda *args, fresh_account=False, **kwargs: dict(quote)
    bot = ArmedAutoTrader(
        manual, lambda: feed, now_ns=lambda: clock[0],
        history_path=tmp_path / "journal.sqlite3",
    )
    bot.arm("1.05", continuous=True, start_worker=False)
    clock[0] = checkpoint + NS
    feed["final_forecast"] = {
        "market_ticker": ticker, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.68,
    }

    bot.tick()

    assert len(client.orders) == 1
    assert client.orders[0]["price"] == "0.8800"
    assert client.orders[0]["count"] == "1.17"
    assert bot.status()["last_result"] == "FILLED"
    assert bot.status()["latest_opportunity"]["route"] == "FORECAST_ONLY_IOC"
    assert Decimal(bot.status()["latest_opportunity"]["expected_net_dollars"]) < 0
    assert bot.history_store.has_attempt(ticker)
    for _ in range(3):
        bot.tick()
    assert len(client.orders) == 1


def test_final_signal_only_still_enforces_five_percent_cash_cap(tmp_path):
    """Removing EV gate must not remove the fresh account exposure ceiling."""
    from types import SimpleNamespace
    from kalshi_edge.trading.manual_trade import ManualTrader

    class NeverPost:
        def send_order(self, payload):
            raise AssertionError("Over-budget final must not POST")

    ticker = "KXBTC15M-FINAL-RISK-CAP"
    clock = [1_900_000_000 * NS]
    close = clock[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {
        "market_ticker": ticker, "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint, "final_forecast": None,
    }
    manual = ManualTrader(
        tmp_path, vault=SimpleNamespace(), client=NeverPost(),
        market_client=SimpleNamespace(),
    )
    quote = {
        "ticker": ticker, "action": "BUY", "outcome": "UP",
        "units": "DOLLARS", "side": "bid", "price": "0.8800",
        "unit_price": "0.8800", "count": "1.17",
        "fee_estimate": "0.010000", "amount_estimate": "1.0396",
        "available_balance": "10.00", "position_yes": "0.00",
    }
    manual._prepare = lambda *args, fresh_account=False, **kwargs: dict(quote)
    bot = ArmedAutoTrader(
        manual, lambda: feed, now_ns=lambda: clock[0],
        history_path=tmp_path / "journal.sqlite3",
    )
    bot.arm("1.05", continuous=True, start_worker=False)
    clock[0] = checkpoint + NS
    feed["final_forecast"] = {
        "market_ticker": ticker, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.68,
    }
    bot.tick()
    assert bot.status()["last_result"] == "NOT_SUBMITTED"
    assert "5% cash" in bot.status()["message"]
