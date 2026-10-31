"""Persistent multi-round AutoTrade: only armed users and locked final calls.

Only simulated market snapshots and order submission are used.
"""
import pytest
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader, AutoTradeError
from test_autotrade_history import Clock, Feed, FakeManual, NS, TICKER


def create(tmp_path):
    clock=Clock();feed=Feed(clock);manual=FakeManual()
    store=tmp_path/"data"/"autotrade_history.sqlite3"
    engine=ArmedAutoTrader(manual,feed,now_ns=clock.ns,history_path=store)
    return clock,feed,manual,engine,store


def rollover(clock, feed, next_ticker):
    feed.ticker=next_ticker
    feed.close += 900*NS
    feed.final=None
    clock.t=feed.close-300*NS


def test_continuous_above_then_below_same_dollars_and_once_per_market(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    s=engine.arm("0.75",continuous=True,start_worker=False)
    assert s["continuous"] is True and s["state"]=="WAITING"
    feed.lock("ABOVE")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="FILLED"
    assert manual.previews==[(TICKER,"BUY","UP","DOLLARS","0.75")]
    for _ in range(4):engine.tick()
    assert len(manual.orders)==1
    ticker2="KXBTC15M-NEXT"
    rollover(clock,feed,ticker2)
    engine.tick()
    assert engine.status()["ticker"]==ticker2
    feed.lock("BELOW")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert manual.previews[-1]==(ticker2,"BUY","DOWN","DOLLARS","0.75")
    assert len(manual.orders)==2
    assert [x["ticker"] for x in engine.history()["entries"]]==[ticker2,TICKER]


def test_skip_and_no_fill_do_not_cancel_continuous_mode(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.lock("SKIP")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="SKIPPED"
    assert manual.orders==[]
    rollover(clock,feed,"KXBTC15M-SECOND")
    def no_fill(pid,ticker):
        manual.orders.append((pid,ticker))
        return {"order_id":"none-1","status":"NO_FILL","fill_count":"0.00"}
    manual.place=no_fill
    feed.lock("ABOVE");engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["last_result"]=="NO_FILL"
    rollover(clock,feed,"KXBTC15M-THIRD")
    feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==2
    assert [x["ticker"] for x in engine.history()["entries"]]==["KXBTC15M-THIRD","KXBTC15M-SECOND"]


def test_market_rollover_without_final_waits_for_new_market(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    engine.arm("0.50",continuous=True,start_worker=False)
    rollover(clock,feed,"KXBTC15M-MISSED")
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert engine.status()["ticker"]=="KXBTC15M-MISSED"
    assert not manual.orders
    feed.lock("ABOVE");engine.tick()
    assert len(manual.orders)==1


def test_late_final_expires_only_current_market_not_session(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    engine.arm("0.50",continuous=True,start_worker=False)
    feed.lock("ABOVE")
    clock.t=feed.close-180*NS
    engine.tick()
    assert engine.status()["state"]=="WAITING"
    assert manual.orders==[]
    rollover(clock,feed,"KXBTC15M-NEW")
    engine.tick();feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==1


def test_cancel_disarms_future_markets(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    engine.arm("0.50",continuous=True,start_worker=False)
    feed.lock("ABOVE");engine.tick()
    engine.cancel()
    rollover(clock,feed,"KXBTC15M-NEXT")
    feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==1
    assert engine.status()["state"]=="CANCELLED"


def test_unknown_order_stops_next_market_and_does_not_retry(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    manual.failure=RuntimeError("lost response after POST")
    engine.arm("0.50",continuous=True,start_worker=False)
    feed.lock("ABOVE");engine.tick()
    assert engine.status()["state"]=="UNKNOWN"
    rollover(clock,feed,"KXBTC15M-NEXT")
    feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==1


def test_restart_does_not_rearm_but_history_prevents_duplicate(tmp_path):
    clock,feed,manual,engine,store=create(tmp_path)
    engine.arm("0.50",continuous=True,start_worker=False)
    feed.lock("ABOVE");engine.tick()
    restarted=ArmedAutoTrader(FakeManual(),feed,now_ns=clock.ns,history_path=store)
    assert restarted.status()["state"]=="IDLE"
    assert len(restarted.history()["entries"])==1


def test_continuous_mode_is_explicit_and_default_one_shot(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    assert engine.arm("1",start_worker=False)["continuous"] is False
    feed.lock();engine.tick()
    assert engine.status()["state"]=="FILLED"


def test_continuous_armed_never_places_before_final(tmp_path):
    clock,feed,manual,engine,_=create(tmp_path)
    engine.arm("1.00",continuous=True,start_worker=False)
    engine.tick()
    assert not manual.orders
    assert engine.status()["state"]=="WAITING"
