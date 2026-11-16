"""The final-only AutoTrade route uses immediate IOC, not an optional maker wait.

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
            return {"order_id": "offline-ioc", "fill_count": order["count"]}
        def get(self, path, *, params=None):
            return {"orders": [{
                "ticker": TICKER, "order_id": order_id,
                "status": "executed",
                "fill_count_fp": self.makers[-1]["count"],
            }], "cursor": ""}
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


def test_negative_taker_ev_still_routes_locked_final_to_immediate_ioc(tmp_path):
    bot, client, _, _ = _setup(tmp_path)
    bot.tick()
    assert client.makers == []
    assert len(client.iocs) == 1
    assert client.iocs[0]["price"] == "0.9200"
    assert bot.status()["last_result"] == "FILLED"
    assert bot.status()["latest_opportunity"]["route"] == "FORECAST_ONLY_IOC"
    assert bot.history_store.has_attempt(TICKER)
    assert client.iocs[0]["client_order_id"] == bot.history_store.recent_stages(TICKER)[0]["client_order_id"]


def test_unprofitable_maker_does_not_veto_final_ioc(tmp_path):
    bot, client, _, _ = _setup(tmp_path, yes_bid="0.8800")
    bot.tick()
    assert client.makers == []
    assert len(client.iocs) == 1
    assert bot.status()["last_result"] == "FILLED"
    assert not bot.history_store.recent_quote_rejections()


def test_maker_sizes_down_to_fractional_kelly_and_five_percent_budget():
    plan = build_maker_plan(
        _book(), "UP", count="0.55", requested="0.50",
        balance="3.45", confidence=0.80, ask_price="0.9200",
    )
    assert plan is not None
    assert Decimal(plan["count"]) < Decimal("0.55")
    assert Decimal(plan["amount_estimate"]) <= Decimal("3.45") * Decimal("0.05")


def test_final_ioc_never_repeats_after_execution_window_expires(tmp_path):
    bot, client, now, checkpoint = _setup(tmp_path, yes_bid="0.8800")
    bot.tick()
    assert len(client.iocs) == 1
    now[0] = checkpoint + 31 * NS
    bot.tick()
    assert len(client.iocs) == 1
    assert bot.status()["last_result"] == "FILLED"
