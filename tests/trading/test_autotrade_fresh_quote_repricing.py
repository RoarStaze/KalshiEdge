"""Regression: armed AutoTrade reprices BUY from a fresh validated book.

Manual 1-click orders remain strict. All exchange submissions use fake clients.
"""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.trading.manual_trade import ManualTrader, OrderNotSubmitted
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

TICKER = "KXBTC15M-FRESH-REQUOTE"
NS = 1_000_000_000


class Exchange:
    def __init__(self):
        self.orders = []
    def send_order(self, order):
        self.orders.append(dict(order))
        return {"order_id":"fake-order", "fill_count":order["count"]}


def plans(outcome):
    if outcome == "UP":
        side, first_yes, next_yes = "bid", "0.9700", "0.9900"
        old_unit, fresh_unit = "0.9700", "0.9900"
    else:
        side, first_yes, next_yes = "ask", "0.0300", "0.0100"
        old_unit, fresh_unit = "0.9700", "0.9900"
    return (
        dict(ticker=TICKER, action="BUY", outcome=outcome,
             units="DOLLARS", side=side, price=first_yes, unit_price=old_unit,
             count="0.10", fee_estimate="0.003000", amount_estimate="0.1000",
             available_balance="3.2100", position_yes="0.00"),
        dict(ticker=TICKER, action="BUY", outcome=outcome,
             units="DOLLARS", side=side, price=next_yes, unit_price=fresh_unit,
             count="0.09", fee_estimate="0.000900", amount_estimate="0.0900",
             available_balance="3.2100", position_yes="0.00"),
    )


@pytest.mark.parametrize("outcome", ["UP", "DOWN"])
def test_autotrade_submits_latest_repriced_budgeted_direction_once(tmp_path, outcome):
    client = Exchange()
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(), client=client, market_client=SimpleNamespace())
    old, new = plans(outcome)
    seen = []
    def prepare(ticker, action, side, units, amount, *, fresh_account=False):
        seen.append(fresh_account)
        return dict(new if fresh_account else old)
    trader._prepare = prepare
    preview = trader.preview(TICKER, "BUY", outcome, "DOLLARS", "0.10")
    result = trader.place_auto(preview["preview_id"], TICKER)
    assert seen == [False, True]
    assert len(client.orders) == 1
    assert client.orders[0]["price"] == new["price"]
    assert client.orders[0]["count"] == new["count"]
    assert client.orders[0]["side"] == new["side"]
    assert Decimal(new["count"]) * Decimal(new["unit_price"]) + Decimal(new["fee_estimate"]) <= Decimal("0.10")
    assert result["requested_count"] == new["count"]
    assert result["estimated_per_share"] == new["unit_price"]
    assert result["status"] == "FILLED"
    with pytest.raises(OrderNotSubmitted, match="Quote expired"):
        trader.place_auto(preview["preview_id"], TICKER)
    assert len(client.orders) == 1


def test_manual_one_click_buy_still_rejects_adverse_price_movement(tmp_path):
    client = Exchange()
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(), client=client, market_client=SimpleNamespace())
    old, new = plans("UP")
    trader._prepare = lambda *args, fresh_account=False, **kwargs: dict(new if fresh_account else old)
    preview = trader.preview(TICKER, "BUY", "UP", "DOLLARS", "0.10")
    with pytest.raises(OrderNotSubmitted, match="quote again"):
        trader.place(preview["preview_id"], TICKER)
    assert client.orders == []


def test_autotrade_repricing_cannot_exceed_original_dollar_budget(tmp_path):
    client = Exchange()
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(), client=client, market_client=SimpleNamespace())
    old, new = plans("UP")
    new.update(count="0.11",fee_estimate="0.001100",amount_estimate="0.1100")
    trader._prepare = lambda *args, fresh_account=False, **kwargs: dict(new if fresh_account else old)
    preview = trader.preview(TICKER, "BUY", "UP", "DOLLARS", "0.10")
    with pytest.raises(OrderNotSubmitted, match="budget"):
        trader.place_auto(preview["preview_id"], TICKER)
    assert client.orders == []


def test_armed_trader_chooses_auto_reprice_path_and_never_reposts(tmp_path):
    clock = [1_900_000_000 * NS]
    close = clock[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {"market_ticker": TICKER, "market_close_ts_ns": close,
            "checkpoint_ts_ns": checkpoint, "final_forecast": None}

    class Fake:
        def __init__(self):
            self.auto = []
        def preview(self, ticker, action, outcome, units, amount):
            return {"ticker": ticker, "action": action, "outcome": outcome,
                    "preview_id": "fake", "unit_price":"0.9900",
                    "count":"0.09", "fee_estimate":"0.000900",
                    "amount_estimate":"0.0900"}
        def place(self, *_):
            raise AssertionError("Armed AutoTrade must not use manual strict place")
        def place_auto(self, preview_id, ticker):
            self.auto.append((preview_id, ticker))
            return {"order_id":"fake-order", "fill_count":"0.09", "status":"FILLED"}

    manual = Fake()
    engine = ArmedAutoTrader(manual,lambda:feed,now_ns=lambda:clock[0],
                              history_path=tmp_path/"journal.sqlite3")
    engine.arm("0.10",continuous=True,start_worker=False)
    clock[0] = checkpoint+NS
    feed["final_forecast"]={"market_ticker": TICKER,"scheduled_ts_ns":checkpoint,
                             "forecast":"ABOVE","confidence":0.96}
    engine.tick()
    assert manual.auto == [("fake",TICKER)]
    assert engine.status()["last_result"] == "FILLED"
    for _ in range(3):
        engine.tick()
    assert len(manual.auto) == 1
