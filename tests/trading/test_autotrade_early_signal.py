"""Early AUTO entry at <=8m remaining and >=90% live confidence.

One armed dollar amount; never more than one signed BUY per BTC market.
All exchange calls are mocked and tested against the real AutoTrade engine.
"""
import pytest
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS=1_000_000_000

class Clock:
    def __init__(self):
        self.t=1_900_000_000*NS
    def now_ns(self):
        return self.t

class Feed:
    def __init__(self,clock):
        self.clock=clock
        self.ticker="KXBTC15M-EARLY01"
        self.close=clock.t+900*NS
        self.final=None
        self.side="ABOVE"
        self.conf=.95
        self.status="LIVE"
        self.prediction_status="OK"
        self.pred_age=1*NS
        self.market_match=True
    def __call__(self):
        now=self.clock.t
        return {"status":self.status,
                "server_time_ns":now,
                "market_ticker":self.ticker,
                "market_close_ts_ns":self.close,
                "checkpoint_ts_ns":self.close-240*NS,
                "final_forecast":self.final,
                "latest_prediction":{
                    "market_ticker":self.ticker if self.market_match else "KXBTC15M-OTHER",
                    "prediction_ts_ns":now-self.pred_age,
                    "status":self.prediction_status,
                    "predicted_side":self.side,
                    "confidence":self.conf,
                }}
    def minutes_left(self,minutes):
        self.clock.t=self.close-round(minutes*60*NS)
    def lock(self,side="ABOVE",confidence=.72):
        self.clock.t=self.close-239*NS
        self.final={"market_ticker":self.ticker,"scheduled_ts_ns":self.close-240*NS,
                    "forecast":side,"confidence":confidence}
    def next_market(self):
        self.close+=900*NS
        self.ticker="KXBTC15M-EARLY02"
        self.final=None
        self.minutes_left(10)

class Exchange:
    def __init__(self):
        self.quoted=[]
        self.orders=[]
        self.on_preview=None
    def preview(self,ticker,action,side,units,amount):
        self.quoted.append((ticker,action,side,units,amount))
        if self.on_preview:
            self.on_preview()
        return {"ticker":ticker,"action":action,"outcome":side,"preview_id":"quoted"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        return {"status":"FILLED","fill_count":"0.50","order_id":f"order-{ticker}"}

@pytest.fixture
def env(tmp_path):
    clock=Clock()
    feed=Feed(clock)
    exchange=Exchange()
    engine=ArmedAutoTrader(exchange,feed,now_ns=clock.now_ns,
                           history_path=tmp_path/"history.sqlite3")
    return clock,feed,exchange,engine

def test_early_entry_above_at_exactly_eight_minutes_and_90pct(env):
    _,feed,orders,engine=env
    engine.arm("1.25",continuous=True,start_worker=False)
    feed.minutes_left(8)
    feed.conf=.90
    engine.tick()
    assert orders.quoted==[("KXBTC15M-EARLY01","BUY","UP","DOLLARS","1.25")]
    assert len(orders.orders)==1
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="FILLED"
    assert engine.history()["entries"][0]["outcome"]=="UP"
    feed.lock(side="BELOW",confidence=.95)
    engine.tick()
    assert len(orders.orders)==1  # no second attempt at final lock

def test_early_down_uses_live_side_and_same_amount_in_next_round(env):
    _,feed,orders,engine=env
    engine.arm(".75",continuous=True,start_worker=False)
    feed.minutes_left(7.5)
    feed.side="BELOW"
    feed.conf=.96
    engine.tick()
    assert orders.quoted[0][2:] == ("DOWN","DOLLARS","0.75")
    feed.next_market()
    engine.tick() # observe next market before entry
    feed.minutes_left(7)
    feed.side="ABOVE"
    engine.tick()
    assert len(orders.orders)==2
    assert orders.quoted[1][2:] == ("UP","DOLLARS","0.75")

@pytest.mark.parametrize("minutes,confidence",[
    (9,.99), (8.01,.99), (8,.899), (7.5,.80), (4.01,.89)
])
def test_no_early_order_before_window_or_below_90pct(env,minutes,confidence):
    _,feed,orders,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.minutes_left(minutes)
    feed.conf=confidence
    engine.tick()
    assert len(orders.orders)==0
    assert engine.status()["state"]=="WAITING"

@pytest.mark.parametrize("condition",["degraded","bad_prediction","stale","future","mismatch","invalid_side"])
def test_bad_or_stale_live_signal_cannot_trigger_early_order(env,condition):
    _,feed,orders,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.minutes_left(7)
    if condition=="degraded":feed.status="DEGRADED"
    if condition=="bad_prediction":feed.prediction_status="NO_PREDICTION"
    if condition=="stale":feed.pred_age=9*NS
    if condition=="future":feed.pred_age=-2*NS
    if condition=="mismatch":feed.market_match=False
    if condition=="invalid_side":feed.side="SKIP"
    engine.tick()
    assert len(orders.orders)==0

def test_cannot_order_when_live_signal_flips_between_quote_and_post(env):
    _,feed,orders,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.minutes_left(7)
    orders.on_preview=lambda:setattr(feed,"side","BELOW")
    engine.tick()
    assert not orders.orders
    assert engine.status()["state"]=="WAITING"  # discard changed signal in this round

def test_final_fallback_buys_if_early_gate_not_met(env):
    _,feed,orders,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.minutes_left(6)
    feed.conf=.50
    engine.tick()
    assert not orders.orders
    feed.lock("BELOW",confidence=.75)
    engine.tick()
    assert orders.quoted==[("KXBTC15M-EARLY01","BUY","DOWN","DOLLARS","1.00")]
    assert len(orders.orders)==1

def test_final_skip_does_not_buy_and_session_stays_armed(env):
    _,feed,orders,engine=env
    engine.arm("0.50",continuous=True,start_worker=False)
    feed.minutes_left(6)
    feed.conf=.85
    engine.tick()
    feed.lock("SKIP",confidence=.40)
    engine.tick()
    assert not orders.orders
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="SKIPPED"

def test_early_cancel_before_eight_minutes_prevents_order(env):
    _,feed,orders,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    engine.cancel()
    feed.minutes_left(7)
    engine.tick()
    assert not orders.orders

def test_quote_at_early_time_does_not_override_market_rollover(env):
    _,feed,orders,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.minutes_left(7)
    orders.on_preview=feed.next_market
    engine.tick()
    assert not orders.orders
    assert engine.status()["state"]=="WAITING"
