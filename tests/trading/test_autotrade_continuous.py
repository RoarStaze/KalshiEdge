"""Continuous AutoTrade: one explicit arm, successive locked KXBTC15M forecasts.

Only simulated order and forecast transports; no real exchange calls.
"""
import pytest
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader, AutoTradeError

NS = 1_000_000_000
TICKER = "KXBTC15M-FIRST"

class Clock:
    def __init__(self):self.t=1_900_000_000*NS
    def now(self):return self.t

class Feed:
    def __init__(self,clock):
        self.clock=clock;self.ticker=TICKER;self.close=clock.now()+900*NS
        self.final=None
    def __call__(self):
        return {"market_ticker":self.ticker,"market_close_ts_ns":self.close,
                "checkpoint_ts_ns":self.close-240*NS,"final_forecast":self.final}
    def lock(self,direction="ABOVE",confidence=0.84):
        self.clock.t=self.close-239*NS
        self.final={"market_ticker":self.ticker,"scheduled_ts_ns":self.close-240*NS,
                    "forecast":direction,"confidence":confidence}
    def rollover(self,ticker):
        self.ticker=ticker
        self.close+=900*NS
        self.final=None
        self.clock.t=self.close-300*NS

class Manual:
    def __init__(self):
        self.previews=[];self.orders=[];self.failure=None;self.status="FILLED"
    def preview(self,ticker,action,side,units,amount):
        self.previews.append((ticker,action,side,units,amount))
        return {"ticker":ticker,"action":action,"outcome":side,"preview_id":"pid"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        if self.failure:raise self.failure
        return {"status":self.status,"order_id":"order-"+ticker,"fill_count":"0.25"}

@pytest.fixture
def env(tmp_path):
    clock=Clock();feed=Feed(clock);manual=Manual()
    engine=ArmedAutoTrader(manual,feed,now_ns=clock.now,
                           history_path=tmp_path/"auto.sqlite3")
    return clock,feed,manual,engine

def test_continuous_forecasts_use_same_amount_and_trade_each_new_market_once(env):
    _,feed,manual,engine=env
    state=engine.arm("0.75",continuous=True,start_worker=False)
    assert state["continuous"] and state["state"]=="WAITING"
    engine.tick()
    assert not manual.orders
    feed.lock("ABOVE")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="FILLED"
    assert manual.previews==[(TICKER,"BUY","UP","DOLLARS","0.75")]
    for i in range(4):engine.tick()
    assert len(manual.orders)==1
    feed.rollover("KXBTC15M-SECOND")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["ticker"]=="KXBTC15M-SECOND"
    feed.lock("BELOW");engine.tick()
    assert manual.previews[-1]==("KXBTC15M-SECOND","BUY","DOWN","DOLLARS","0.75")
    assert len(manual.orders)==2
    assert [r["ticker"] for r in engine.history()["entries"]]==["KXBTC15M-SECOND",TICKER]

def test_skips_no_fill_and_expiry_keep_session_armed(env):
    clock,feed,manual,engine=env
    engine.arm("1.25",continuous=True,start_worker=False)
    feed.lock("SKIP");engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="SKIPPED"
    assert not manual.orders
    feed.rollover("KXBTC15M-NEXT")
    manual.status="NO_FILL";feed.lock("ABOVE");engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="NO_FILL"
    feed.rollover("KXBTC15M-MISSED")
    clock.t=feed.close-180*NS
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="EXPIRED"
    feed.rollover("KXBTC15M-FOURTH")
    manual.status="FILLED";feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==2

def test_market_change_before_forecast_does_not_cancel(env):
    _,feed,manual,engine=env
    engine.arm("0.50",continuous=True,start_worker=False)
    feed.rollover("KXBTC15M-SECOND");engine.tick()
    assert engine.status()["ticker"]=="KXBTC15M-SECOND"
    assert engine.status()["state"]=="WAITING"
    assert not manual.orders
    feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==1

def test_can_arm_late_in_round_but_waits_for_next(env):
    _,feed,manual,engine=env
    feed.lock()
    state=engine.arm("0.10",continuous=True,start_worker=False)
    assert state["state"]=="WAITING"
    engine.tick()
    assert not manual.orders
    feed.rollover("KXBTC15M-SECOND");engine.tick()
    feed.lock();engine.tick()
    assert len(manual.orders)==1
    assert manual.previews[0][0]=="KXBTC15M-SECOND"

def test_cancel_disables_future_markets(env):
    _,feed,manual,engine=env
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.lock();engine.tick()
    assert engine.cancel()["state"]=="CANCELLED"
    feed.rollover("KXBTC15M-SECOND");feed.lock();engine.tick()
    assert len(manual.orders)==1

def test_unknown_exchange_result_stops_recurring_mode(env):
    _,feed,manual,engine=env
    manual.failure=TimeoutError("exchange may have accepted the POST")
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.lock();engine.tick()
    assert engine.status()["state"]=="UNKNOWN"
    feed.rollover("KXBTC15M-SECOND");feed.lock();engine.tick()
    assert len(manual.orders)==1

def test_fresh_next_market_must_be_before_checkpoint_to_be_eligible(env):
    clock,feed,manual,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.rollover("KXBTC15M-SECOND")
    feed.lock() # new round already locked when first observed
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert not manual.orders
    feed.rollover("KXBTC15M-THIRD");engine.tick()
    feed.lock();engine.tick()
    assert len(manual.orders)==1
    assert manual.previews[0][0]=="KXBTC15M-THIRD"

def test_history_prevents_duplicate_after_rearm_restart(env,tmp_path):
    clock,feed,manual,engine=env
    engine.arm("1",continuous=True,start_worker=False)
    feed.lock();engine.tick()
    engine.cancel()
    another=ArmedAutoTrader(Manual(),feed,now_ns=clock.now,
                            history_path=tmp_path/"auto.sqlite3")
    assert another.status()["state"]=="IDLE"
    state=another.arm("1",continuous=True,start_worker=False)
    another.tick()
    assert state["state"]=="WAITING"
    assert another.history()["entries"][0]["ticker"]==TICKER
    assert not another.manual.orders

def test_default_direct_engine_arm_is_still_one_shot(env):
    _,feed,manual,engine=env
    assert engine.arm("1",start_worker=False)["continuous"] is False
    feed.lock();engine.tick()
    assert engine.status()["state"]=="FILLED"

def test_bad_continuous_flag_is_not_accepted(env):
    _,_,_,engine=env
    with pytest.raises(AutoTradeError):
        engine.arm("1",continuous="true",start_worker=False)
