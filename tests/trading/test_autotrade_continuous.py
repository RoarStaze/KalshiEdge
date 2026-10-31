"""Opt-in continuous BTC AutoTrade after each independently locked final forecast.

The engine is tested against fake forecasts and fake exchange orders only.
"""
import threading
import pytest

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader, AutoTradeError

NS=1_000_000_000


class Clock:
    def __init__(self):
        self.t=1_900_000_000*NS
    def ns(self):
        return self.t


class Market:
    def __init__(self, clock):
        self.clock=clock
        self.number=1
        self.ticker="KXBTC15M-ROUND1"
        self.close=clock.ns()+900*NS
        self.final=None
        self.missing=False
    def __call__(self):
        if self.missing:
            return {}
        return {
            "market_ticker":self.ticker,
            "market_close_ts_ns":self.close,
            "checkpoint_ts_ns":self.close-240*NS,
            "final_forecast":self.final,
        }
    def lock(self, outcome="ABOVE",confidence=0.90):
        self.clock.t=self.close-239*NS
        self.final={
            "market_ticker":self.ticker,
            "scheduled_ts_ns":self.close-240*NS,
            "forecast":outcome,
            "confidence":confidence,
        }
    def next_market(self):
        self.number+=1
        self.ticker=f"KXBTC15M-ROUND{self.number}"
        self.close+=900*NS
        self.final=None
        self.clock.t=self.close-300*NS


class Orders:
    def __init__(self):
        self.previews=[]
        self.orders=[]
        self.status="FILLED"
        self.fail=None
    def preview(self,ticker,action,side,units,amount):
        self.previews.append((ticker,action,side,units,amount))
        return {"ticker":ticker,"action":"BUY","outcome":side,"preview_id":f"q-{ticker}",
                "count":"0.25", "unit_price":"0.4000",
                "fee_estimate":"0.005000", "amount_estimate":"0.1050"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        if self.fail is not None:
            raise self.fail
        return {"status":self.status,"order_id":f"order-{ticker}",
                "fill_count":"0.00" if self.status=="NO_FILL" else "0.25"}


@pytest.fixture
def suite(tmp_path):
    clk=Clock()
    market=Market(clk)
    orders=Orders()
    engine=ArmedAutoTrader(orders,market,now_ns=clk.ns,
        history_path=tmp_path/"auto.sqlite3")
    return clk,market,orders,engine


def test_continuous_rounds_trade_opposite_sides_with_same_amount(suite):
    _,market,orders,engine=suite
    status=engine.arm("0.70",continuous=True,start_worker=False)
    assert status["state"]=="WAITING"
    assert status["continuous"] is True
    market.lock("ABOVE")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="FILLED"
    assert orders.previews==[("KXBTC15M-ROUND1","BUY","UP","DOLLARS","0.70")]
    for _ in range(5):
        engine.tick()
    assert len(orders.orders)==1
    market.next_market()
    engine.tick()
    assert engine.status()["ticker"]=="KXBTC15M-ROUND2"
    market.lock("BELOW")
    engine.tick()
    assert orders.previews[-1]==("KXBTC15M-ROUND2","BUY","DOWN","DOLLARS","0.70")
    assert len(orders.orders)==2
    assert len(engine.history()["entries"])==2


def test_skip_and_no_fill_keep_continuous_session_armed(suite):
    _,market,orders,engine=suite
    engine.arm("1.00",continuous=True,start_worker=False)
    market.lock("SKIP")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="SKIPPED"
    assert not orders.orders
    market.next_market()
    orders.status="NO_FILL"
    engine.tick()
    market.lock("ABOVE")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="NO_FILL"
    market.next_market()
    engine.tick()
    market.lock("BELOW")
    engine.tick()
    assert len(orders.orders)==2


def test_market_rollover_without_final_does_not_cancel(suite):
    _,market,orders,engine=suite
    engine.arm("0.25",continuous=True,start_worker=False)
    market.next_market()
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["ticker"]=="KXBTC15M-ROUND2"
    market.lock("ABOVE")
    engine.tick()
    assert len(orders.orders)==1


def test_delayed_forecast_skips_current_round_but_trades_next(suite):
    clock,market,orders,engine=suite
    engine.arm("0.25",continuous=True,start_worker=False)
    market.lock()
    clock.t=market.close-180*NS
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="EXPIRED"
    assert not orders.orders
    market.next_market()
    engine.tick()
    market.lock("BELOW")
    engine.tick()
    assert len(orders.orders)==1


def test_cancel_prevents_all_later_market_trades(suite):
    _,market,orders,engine=suite
    engine.arm("0.25",continuous=True,start_worker=False)
    market.lock("ABOVE")
    engine.tick()
    assert engine.cancel()["state"]=="CANCELLED"
    market.next_market()
    market.lock("BELOW")
    engine.tick()
    assert len(orders.orders)==1


def test_unknown_exchange_result_stops_continuous_mode(suite):
    _,market,orders,engine=suite
    orders.fail=OSError("response lost after exchange accepted POST")
    engine.arm("0.25",continuous=True,start_worker=False)
    market.lock()
    engine.tick()
    assert engine.status()["state"]=="UNKNOWN"
    market.next_market()
    market.lock("BELOW")
    engine.tick()
    assert len(orders.orders)==1


def test_temporary_feed_gap_does_not_disarm_or_submit(suite):
    _,market,orders,engine=suite
    engine.arm("1.00",continuous=True,start_worker=False)
    market.missing=True
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert not orders.orders
    market.missing=False
    market.next_market()
    engine.tick()
    market.lock()
    engine.tick()
    assert len(orders.orders)==1


def test_new_market_already_locked_is_never_backfilled(suite):
    _,market,orders,engine=suite
    engine.arm("0.75",continuous=True,start_worker=False)
    market.next_market()
    market.lock()
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert not orders.orders
    market.next_market()
    engine.tick()
    market.lock("BELOW")
    engine.tick()
    assert len(orders.orders)==1


def test_history_blocks_replay_after_rearm_same_market(suite):
    _,market,orders,engine=suite
    engine.arm("1",continuous=True,start_worker=False)
    market.lock()
    engine.tick()
    engine.cancel()
    with pytest.raises(AutoTradeError,match="already used"):
        engine.arm("1",start_worker=False)
    restarted=ArmedAutoTrader(Orders(),market,now_ns=engine.now_ns,
        history_path=engine.history_store.path)
    assert restarted.status()["state"]=="IDLE"
    assert restarted.history()["entries"][0]["ticker"]==market.ticker


def test_one_shot_default_still_exits_when_filled(suite):
    _,market,orders,engine=suite
    assert engine.arm("1",start_worker=False)["continuous"] is False
    market.lock()
    engine.tick()
    assert engine.status()["state"]=="FILLED"


def test_several_simultaneous_ticks_never_duplicate_round_order(suite):
    _,market,orders,engine=suite
    engine.arm("1",continuous=True,start_worker=False)
    market.lock()
    threads=[threading.Thread(target=engine.tick) for _ in range(8)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert len(orders.orders)==1


def test_partial_fill_still_progresses_to_next_market(suite):
    _,market,orders,engine=suite
    orders.status="PARTIAL"
    engine.arm("0.50",continuous=True,start_worker=False)
    market.lock()
    engine.tick()
    assert engine.status()["last_result"]=="PARTIAL"
    market.next_market()
    engine.tick()
    market.lock("BELOW")
    engine.tick()
    assert len(orders.orders)==2
