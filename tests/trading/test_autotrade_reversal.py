"""Final-only strategy cannot initiate early positions or reversals.

Historical early/reversal journal schemas remain readable; these live tests
use fake exchange responses and never send any real Kalshi orders.
"""
import pytest
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS=1_000_000_000


class Clock:
    def __init__(self):self.t=1_900_000_000*NS
    def now(self):return self.t


class Feed:
    def __init__(self,clock):
        self.clock=clock
        self.ticker="KXBTC15M-REVERSE1"
        self.close=clock.t+900*NS
        self.final=None
        self.prediction_side="ABOVE"
    def __call__(self):
        return {"market_ticker":self.ticker,"market_close_ts_ns":self.close,
                "checkpoint_ts_ns":self.close-480*NS,"status":"LIVE",
                "latest_prediction":{
                   "market_ticker":self.ticker,"status":"OK",
                   "prediction_ts_ns":self.clock.t,
                   "predicted_side":self.prediction_side,"confidence":1.0,
                },
                "final_forecast":self.final}
    def set_before_lock(self):
        self.clock.t=self.close-510*NS
    def lock(self,side="ABOVE",confidence=.85):
        self.clock.t=self.close-479*NS
        self.final={"market_ticker":self.ticker,
                    "checkpoint_seconds":480,
                    "scheduled_ts_ns":self.close-480*NS,
                    "forecast":side,"confidence":confidence}


class Exchange:
    def __init__(self):
        self.orders=[];self.previews=[]
        self.results=[]
    def preview(self,ticker,action,side,units,amount):
        self.previews.append((action,side,units,str(amount)))
        assert action=="BUY"
        return {"ticker":ticker,"action":action,"outcome":side,
                "preview_id":"quote", "count":"0.25","unit_price":"0.4000",
                "fee_estimate":"0.005000","amount_estimate":"0.1050"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        if self.results:
            return self.results.pop(0)
        return {"status":"FILLED","fill_count":"0.25","order_id":"filled"}


@pytest.fixture
def env(tmp_path):
    clock=Clock();feed=Feed(clock);exchange=Exchange()
    engine=ArmedAutoTrader(exchange,feed,now_ns=clock.now,
                           history_path=tmp_path/"history.sqlite3")
    return clock,feed,exchange,engine


@pytest.mark.parametrize("live_side,final_side,expected",[
    ("ABOVE","BELOW","DOWN"),("BELOW","ABOVE","UP"),
])
def test_only_final_direction_can_be_bought_and_never_auto_sold(env,live_side,final_side,expected):
    _,feed,exchange,engine=env
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.set_before_lock()
    feed.prediction_side=live_side
    engine.tick()
    assert exchange.orders==[]
    feed.lock(final_side)
    engine.tick()
    assert exchange.previews==[("BUY",expected,"DOLLARS","0.75")]
    feed.prediction_side=live_side
    engine.tick()
    assert len(exchange.orders)==1
    assert [x["stage"] for x in engine.history_store.recent_stages(feed.ticker)]==["FINAL_BUY"]


def test_final_no_fill_retries_only_confirmed_zero_fill_and_never_switches_direction(env):
    clock,feed,exchange,engine=env
    exchange.results=[
        {"status":"NO_FILL","fill_count":"0.00","order_id":"zero"},
        {"status":"FILLED","fill_count":"0.25","order_id":"filled"},
    ]
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.lock("BELOW")
    engine.tick()
    assert len(exchange.orders)==1
    assert engine.status()["last_result"]=="NO_FILL"
    feed.prediction_side="ABOVE"
    clock.t+=2*NS
    engine.tick()
    assert exchange.previews==[
        ("BUY","DOWN","DOLLARS","0.75"),
        ("BUY","DOWN","DOLLARS","0.75"),
    ]
    assert engine.status()["last_result"]=="FILLED"
    assert [x["stage"] for x in engine.history_store.recent_stages(feed.ticker)]==["FINAL_BUY","FINAL_BUY_2"]


def test_unknown_network_result_stops_without_retry(env):
    _,feed,exchange,engine=env
    def unknown(*args):raise OSError("Kalshi may have accepted POST")
    exchange.place=unknown
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.lock("ABOVE");engine.tick()
    assert engine.status()["state"]=="UNKNOWN"
    assert len(exchange.previews)==1
    engine.tick()
    assert len(exchange.previews)==1


def test_partial_final_fill_is_terminal_without_second_buy_or_sell(env):
    _,feed,exchange,engine=env
    exchange.results=[{"status":"PARTIAL","fill_count":"0.10","order_id":"partial"}]
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.lock("ABOVE");engine.tick()
    assert engine.status()["last_result"]=="PARTIAL"
    feed.prediction_side="BELOW"
    for _ in range(3):engine.tick()
    assert len(exchange.orders)==1
    assert all(p[0]=="BUY" for p in exchange.previews)


def test_low_confidence_or_skip_blocks_final_buy_even_if_live_signal_is_strong(env):
    _,feed,exchange,engine=env
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.lock("BELOW",confidence=.60);engine.tick()
    assert engine.status()["last_result"]=="SKIPPED"
    assert not exchange.orders


def test_cancel_before_final_lock_prevents_any_trade(env):
    _,feed,exchange,engine=env
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.set_before_lock();engine.tick()
    engine.cancel()
    feed.lock("BELOW");engine.tick()
    assert not exchange.orders
