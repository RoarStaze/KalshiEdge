"""Regression: evaluate maker EV before rejecting an expensive taker quote.

All orders and reads are fakes. No live Kalshi credentials or network.
"""
from decimal import Decimal
from types import SimpleNamespace
import uuid

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
from kalshi_edge.trading.manual_trade import ManualTrader
from kalshi_edge.trading.maker_entry import build_maker_plan

NS = 1_000_000_000
TICKER = "KXBTC15M-MAKER-EV"


def _book(yes_bid="0.7000"):
    return {"orderbook_fp": {
        "yes_dollars": [[yes_bid, "2.00"]],
        "no_dollars": [["0.0800", "2.00"]],
    }}


def _setup(tmp_path, yes_bid="0.7000"):
    order_id = str(uuid.uuid4())

    class Client:
        def __init__(self):
            self.makers = []
            self.iocs = []
        def send_resting_order(self, order, *, expiration_time):
            self.makers.append(dict(order))
            return {"order_id": order_id, "fill_count": "0.00"}
        def send_order(self, order):
            self.iocs.append(dict(order))
            raise AssertionError("Should not submit an expensive taker order")
        def get(self, path, *, params=None):
            return {"order": {
                "ticker": TICKER, "order_id": order_id,
                "status": "executed",
                "fill_count_fp": self.makers[-1]["count"],
            }}
        def cancel_resting_order(self, order, *, market_ticker):
            raise AssertionError("Fully executed maker should not be cancelled")

    client = Client()
    market_client = SimpleNamespace(quote=lambda ticker: (
        {"market": {"ticker": ticker, "status": "active"}}, _book(yes_bid)
    ))
    manual = ManualTrader(tmp_path, vault=SimpleNamespace(),
                          client=client, market_client=market_client)
    plan = {
        "ticker": TICKER, "action": "BUY", "outcome": "UP",
        "units": "DOLLARS", "side": "bid", "price": "0.9200",
        "unit_price": "0.9200", "count": "0.15",
        "fee_estimate": "0.003000", "amount_estimate": "0.1410",
        "available_balance": "3.45", "position_yes": "0.00",
    }
    manual._prepare = lambda *args, fresh_account=False, **kwargs: dict(plan)
    now = [1_900_000_000 * NS]
    close = now[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {
        "market_ticker": TICKER, "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint, "final_forecast": None,
    }
    bot = ArmedAutoTrader(manual, lambda: feed, now_ns=lambda: now[0],
                          history_path=tmp_path / "journal.sqlite3")
    bot.arm("0.15", continuous=True, start_worker=False)
    now[0] = checkpoint + NS
    feed["final_forecast"] = {
        "market_ticker": TICKER, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.80,
    }
    return bot, client, now, checkpoint


def test_profitable_maker_is_considered_when_taker_ev_is_negative(tmp_path):
    bot, client, now, checkpoint = _setup(tmp_path)
    bot.tick()
    assert len(client.makers) == 1
    assert client.makers[0]["price"] == "0.7000"
    assert client.iocs == []
    assert bot.status()["last_result"] == "FILLED"
    assert bot.history_store.has_attempt(TICKER)


def test_truly_unprofitable_maker_still_never_places_an_order(tmp_path):
    bot, client, now, checkpoint = _setup(tmp_path, yes_bid="0.8800")
    bot.tick()
    assert client.makers == []
    assert client.iocs == []
    assert not bot.history_store.has_attempt(TICKER)
    assert bot.history_store.recent_quote_rejections()


def test_maker_sizes_down_to_fractional_kelly_and_five_percent_budget():
    plan = build_maker_plan(
        _book(), "UP", count="0.55", requested="0.50",
        balance="3.45", confidence=0.80, ask_price="0.9200",
    )
    assert plan is not None
    assert Decimal(plan["count"]) < Decimal("0.55")
    assert Decimal(plan["amount_estimate"]) <= Decimal("3.45") * Decimal("0.05")


def test_expiration_explains_unsuitable_price_instead_of_only_too_late(tmp_path):
    bot, client, now, checkpoint = _setup(tmp_path, yes_bid="0.8800")
    bot.tick()
    now[0] = checkpoint + 31 * NS
    bot.tick()
    assert bot.status()["last_result"] == "EXPIRED"
    assert "positive" in bot.status()["message"].lower() or "edge" in bot.status()["message"].lower()
