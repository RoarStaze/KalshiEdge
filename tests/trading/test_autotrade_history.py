"""AutoTrade history is persisted by market, never inferred from forecasts.

No real Kalshi requests are made by these tests.
"""
from decimal import Decimal

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
NS=1_000_000_000
TICKER="KXBTC15M-TEST"


class Clock:
    def __init__(self):self.t=1900000000*NS
    def ns(self):return self.t


class Feed:
    def __init__(self,clock):
        self.clock=clock;self.ticker=TICKER
        self.close=clock.ns()+900*NS;self.final=None
    def __call__(self):
        return {"market_ticker":self.ticker,"market_close_ts_ns":self.close,
                "checkpoint_ts_ns":self.close-240*NS,"final_forecast":self.final}
    def lock(self,direction="ABOVE",confidence=0.87):
        self.clock.t=self.close-239*NS
        self.final={"market_ticker":self.ticker,"forecast":direction,
                    "confidence":confidence,"scheduled_ts_ns":self.close-240*NS}


class FakeManual:
    def __init__(self):self.previews=[];self.orders=[];self.failure=None
    def preview(self,ticker,action,outcome,units,amount):
        self.previews.append((ticker,action,outcome,units,amount))
        return {"ticker":ticker,"action":action,"outcome":outcome,"preview_id":"preview-123"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        if self.failure:raise self.failure
        return {"order_id":"exchange-123","status":"FILLED","fill_count":"0.15"}



def setup(tmp_path):
    clock=Clock()
    feed=Feed(clock)
    manual=FakeManual()
    store=tmp_path / "data" / "autotrade_history.sqlite3"
    engine=ArmedAutoTrader(manual,feed,now_ns=clock.ns,history_path=store)
    return clock,feed,manual,engine,store


def test_one_record_per_actual_order_attempt_persists_across_restart(tmp_path):
    clock,feed,manual,engine,store=setup(tmp_path)
    assert engine.history()["entries"]==[]
    engine.arm("1.25",start_worker=False)
    assert engine.history()["entries"]==[]  # arming is not an order
    feed.lock("ABOVE",0.87)
    engine.tick()
    rows=engine.history()["entries"]
    assert len(rows)==1
    item=rows[0]
    assert item["ticker"]==TICKER
    assert item["outcome"]=="UP"
    assert item["amount"]=="1.25"
    assert item["confidence"]==0.87
    assert item["status"]=="FILLED"
    assert item["order_id"]=="exchange-123"
    assert item["fill_count"]=="0.15"
    assert item["attempted_at_ns"]==clock.ns()
    assert len(manual.orders)==1
    engine2=ArmedAutoTrader(FakeManual(),feed,now_ns=clock.ns,history_path=store)
    assert engine2.history()==engine.history()
    feed.final=None
    clock.t=feed.close-300*NS
    try:
        engine2.arm("1.25",start_worker=False)
    except ValueError as exc:
        assert "already used" in str(exc)
    else:
        raise AssertionError("Restart must not allow a repeat order for the same ticker")


def test_below_fill_records_down_not_just_a_forecast(tmp_path):
    _,feed,manual,engine,_=setup(tmp_path)
    engine.arm("0.10",start_worker=False)
    feed.lock("BELOW")
    engine.tick()
    assert engine.history()["entries"][0]["outcome"]=="DOWN"
    assert manual.previews[0][2]=="DOWN"


def test_cancellation_and_skip_are_not_reported_as_trades(tmp_path):
    clock,feed,_,engine,_=setup(tmp_path)
    engine.arm("1",start_worker=False)
    engine.cancel()
    assert engine.history()["entries"]==[]
    another=ArmedAutoTrader(FakeManual(),feed,now_ns=clock.ns,
                            history_path=tmp_path/"data"/"autotrade_history.sqlite3")
    another.arm("1",start_worker=False)
    feed.lock("SKIP")
    another.tick()
    assert another.history()["entries"]==[]


def test_network_failure_records_unknown_not_filled(tmp_path):
    _,feed,manual,engine,_=setup(tmp_path)
    manual.failure=OSError("network lost after POST")
    engine.arm("0.75",start_worker=False)
    feed.lock("ABOVE")
    engine.tick()
    row=engine.history()["entries"][0]
    assert row["status"]=="UNKNOWN"
    assert row["fill_count"] is None
    assert row["order_id"] is None
    engine.tick()
    assert len(manual.orders)==1
    assert len(engine.history()["entries"])==1


def test_no_fill_is_visible_but_not_a_filled_trade(tmp_path):
    _,feed,manual,engine,_=setup(tmp_path)
    def no_fill(_preview,_ticker):
        manual.orders.append((_preview,_ticker))
        return {"status":"NO_FILL","order_id":"none-123","fill_count":"0.00"}
    manual.place=no_fill
    engine.arm("0.25",start_worker=False)
    feed.lock()
    engine.tick()
    row=engine.history()["entries"][0]
    assert row["status"]=="NO_FILL"
    assert row["fill_count"]=="0.00"
    assert row["order_id"]=="none-123"


def test_history_write_failure_blocks_real_order(tmp_path):
    _,feed,manual,engine,_=setup(tmp_path)
    class BrokenStore:
        def has_attempt(self,ticker):return False
        def record_attempt(self,**kwargs):raise OSError("read-only disk")
        def recent(self,limit=25):return []
    engine.history_store=BrokenStore()
    engine.arm("1",start_worker=False)
    feed.lock()
    engine.tick()
    assert not manual.orders
    assert engine.status()["state"]=="ERROR"


def test_history_only_in_auto_mode_at_bottom_of_card():
    page=integrate_manual_trade(PAGE)
    assert 'id="manual-auto-history"' in page
    assert 'id="manual-auto-history-list"' in page
    assert 'AutoTrade History' in page
    assert page.index('id="manual-auto-history"') > page.index('id="manual-auto-arm"')
    assert 'class="mt-auto-history mt-hidden"' in page
    assert 'byId("manual-auto-history").classList.toggle("mt-hidden",!selected)' in page
    assert 'autoJSON("history")' in page
    assert 'document.createElement("div")' in page
    assert 'textContent' in page
    assert 'if(autoSelected)' in page  # no history polling in manual mode
