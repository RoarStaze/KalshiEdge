"""Real-money AutoTrade is opt-in, one-shot, and forecast-gated.

All exchange interactions in these tests use in-memory fakes.
"""
from decimal import Decimal
import threading

import pytest

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader, AutoTradeError


NS = 1_000_000_000
TICKER = "KXBTC15M-TEST"


class Clock:
    def __init__(self):
        self.t = 1900000000 * NS
    def ns(self):
        return self.t


class Feed:
    def __init__(self, clock):
        self.clock=clock
        self.ticker=TICKER
        self.close=clock.ns()+900*NS
        self.final=None
        self.status="LIVE"
    def __call__(self):
        return {"market_ticker":self.ticker, "market_close_ts_ns":self.close,
                "checkpoint_ts_ns":self.close-240*NS,
                "status":self.status, "final_forecast":self.final}
    def lock(self, side="ABOVE", confidence=.83):
        self.clock.t=self.close-239*NS
        self.final={"market_ticker":self.ticker, "forecast":side,
                    "scheduled_ts_ns":self.close-240*NS,
                    "confidence":confidence, "reason":None}


class FakeManual:
    def __init__(self):
        self.previews=[]
        self.orders=[]
        self.failure=None
    def preview(self,ticker,action,outcome,units,amount):
        self.previews.append((ticker,action,outcome,units,str(amount)))
        return {"ticker":ticker,"action":action,"outcome":outcome,
                "preview_id":"preview-once", "count":"0.15",
                "unit_price":"0.4000", "fee_estimate":"0.003000",
                "amount_estimate":"0.0630"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        if self.failure:
            raise self.failure
        return {"ticker":ticker,"order_id":"exchange-123",
                "status":"FILLED", "fill_count":"0.15"}


@pytest.fixture
def env():
    clock=Clock()
    feed=Feed(clock)
    manual=FakeManual()
    engine=ArmedAutoTrader(manual,feed,now_ns=clock.ns)
    return clock,feed,manual,engine


def test_arm_is_explicit_and_waits_before_checkpoint(env):
    _,feed,manual,engine=env
    result=engine.arm("1.25",start_worker=False)
    assert result["state"]=="WAITING"
    assert result["ticker"]==TICKER
    assert result["amount"]=="1.25"
    assert not manual.orders and not manual.previews
    engine.tick()
    assert not manual.orders


@pytest.mark.parametrize("direction,expected",[("ABOVE","UP"),("BELOW","DOWN")])
def test_locked_final_places_exactly_one_matching_buy(env,direction,expected):
    _,feed,manual,engine=env
    engine.arm("0.25",start_worker=False)
    feed.lock(direction)
    engine.tick()
    assert manual.previews==[(TICKER,"BUY",expected,"DOLLARS","0.25")]
    assert manual.orders==[("preview-once",TICKER)]
    assert engine.status()["state"]=="FILLED"
    for _ in range(5):
        engine.tick()
    assert len(manual.orders)==1


@pytest.mark.parametrize("direction,confidence",[
    ("SKIP",.90),("ABOVE",.68),("BELOW",None)])
def test_skip_low_confidence_or_missing_confidence_never_trades(env,direction,confidence):
    _,feed,manual,engine=env
    engine.arm("1.00",start_worker=False)
    feed.lock(direction,confidence)
    engine.tick()
    assert engine.status()["state"]=="SKIPPED"
    assert not manual.previews and not manual.orders


def test_cannot_arm_after_final_or_past_checkpoint(env):
    clock,feed,manual,engine=env
    feed.lock("ABOVE")
    with pytest.raises(AutoTradeError, match="already locked|checkpoint"):
        engine.arm("1",start_worker=False)
    feed.final=None
    with pytest.raises(AutoTradeError, match="checkpoint"):
        engine.arm("1",start_worker=False)
    assert not manual.orders


def test_wrong_ticker_or_bad_final_linkage_never_trades(env):
    clock,feed,manual,engine=env
    engine.arm("2",start_worker=False)
    feed.lock()
    feed.final["market_ticker"]="KXBTC15M-OTHER"
    engine.tick()
    assert not manual.orders
    assert engine.status()["state"]=="WAITING"
    feed.ticker="KXBTC15M-OTHER"
    engine.tick()
    assert engine.status()["state"]=="EXPIRED"
    assert not manual.orders


def test_cancel_before_lock_disarms(env):
    _,feed,manual,engine=env
    engine.arm("1.00",start_worker=False)
    assert engine.cancel()["state"]=="CANCELLED"
    feed.lock("BELOW")
    engine.tick()
    assert not manual.orders


def test_auto_trading_rejects_invalid_or_missing_amount(env):
    _,_,_,engine=env
    for s in ("","0","-2","NaN","Infinity","100001","abc"):
        with pytest.raises(AutoTradeError):
            engine.arm(s,start_worker=False)


def test_repeated_arm_cannot_overwrite_pending_or_repeat_order(env):
    _,feed,manual,engine=env
    engine.arm("1",start_worker=False)
    with pytest.raises(AutoTradeError, match="already armed"):
        engine.arm("2",start_worker=False)
    feed.lock()
    engine.tick()
    with pytest.raises(AutoTradeError, match="already used"):
        engine.arm("2",start_worker=False)
    assert len(manual.orders)==1


def test_exchange_failure_is_terminal_and_never_retried(env):
    _,feed,manual,engine=env
    manual.failure=RuntimeError("unknown network outcome")
    engine.arm("1",start_worker=False)
    feed.lock()
    engine.tick()
    assert engine.status()["state"]=="UNKNOWN"
    engine.tick()
    assert len(manual.orders)==1
    with pytest.raises(AutoTradeError, match="already used"):
        engine.arm("1",start_worker=False)


def test_final_too_late_cannot_submit(env):
    clock,feed,manual,engine=env
    engine.arm("1",start_worker=False)
    feed.lock()
    clock.t=feed.close-180*NS
    engine.tick()
    assert engine.status()["state"]=="EXPIRED"
    assert not manual.orders


def test_changed_market_before_order_cancels_without_sending(env):
    _,feed,manual,engine=env
    engine.arm("1",start_worker=False)
    feed.lock()
    def preview(ticker,action,outcome,units,amount):
        feed.ticker="KXBTC15M-NEXT"
        return {"ticker":ticker,"action":action,"outcome":outcome,
                "preview_id":"preview-once","count":"0.15",
                "unit_price":"0.4000", "fee_estimate":"0.003000",
                "amount_estimate":"0.0630"}
    manual.preview=preview
    engine.tick()
    assert not manual.orders
    assert engine.status()["state"]=="EXPIRED"


def test_simultaneous_ticks_submit_once(env):
    _,feed,manual,engine=env
    engine.arm("1",start_worker=False)
    feed.lock()
    threads=[threading.Thread(target=engine.tick) for _ in range(6)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert len(manual.orders)==1
