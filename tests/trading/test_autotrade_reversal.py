"""Reversal and NO_FILL reconciliation tests use only fake exchange calls."""
import pytest
from decimal import Decimal
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS = 1_000_000_000

class Clock:
    def __init__(self): self.t=1900000000*NS
    def now(self): return self.t

class Feed:
    def __init__(self,c):
        self.c=c;self.close=c.t+900*NS;self.ticker="KXBTC15M-REVERSE1"
        self.final=None
        self.side="ABOVE";self.conf=.95
    def __call__(self):
        return {"market_ticker":self.ticker,"market_close_ts_ns":self.close,
                "checkpoint_ts_ns":self.close-240*NS,"status":"LIVE",
                "latest_prediction":{
                    "market_ticker":self.ticker,"status":"OK",
                    "prediction_ts_ns":self.c.t-NS,
                    "predicted_side":self.side,"confidence":self.conf},
                "final_forecast":self.final}
    def early(self):
        self.c.t=self.close-420*NS
    def lock(self,side):
        self.c.t=self.close-239*NS
        self.final={"market_ticker":self.ticker,"scheduled_ts_ns":self.close-240*NS,
                    "forecast":side,"confidence":.81}

class Trader:
    def __init__(self):
        self.orders=[];self.quoted=[];self.fill="0.40"
        self.position=Decimal("0")
        self.sell_status="FILLED"
        self.first_buy_status="FILLED"
        self.action_count=0
    def _account(self,ticker,*,fresh=False):
        return Decimal("100"),self.position
    def market(self,ticker):
        return {"ticker":ticker,"position_yes":str(self.position)}
    def preview(self,ticker,action,outcome,units,amount):
        self.quoted.append((ticker,action,outcome,units,str(amount)))
        return {"ticker":ticker,"action":action,"outcome":outcome,
                "preview_id":f"preview-{len(self.quoted)}",
                "count":str(amount) if units=="SHARES" else self.fill,
                "price":"0.5000", "side":"bid" if outcome=="UP" else "ask"}
    def place(self,preview_id,ticker):
        _,action,side,units,amount=self.quoted[-1]
        self.orders.append((action,side,amount))
        if action=="SELL":
            if self.sell_status=="NO_FILL":
                return {"order_id":"sell-no-fill","status":"NO_FILL","fill_count":"0.00"}
            if self.sell_status=="PARTIAL":
                self.position-=Decimal(amount if side=="UP" else "0")/2
                return {"order_id":"sell-partial","status":"PARTIAL","fill_count":str(Decimal(amount)/2)}
            qty=Decimal(amount)
            self.position-=qty if side=="UP" else -qty
            return {"order_id":"sell-complete","status":"FILLED","fill_count":str(qty)}
        if len([o for o in self.orders if o[0]=="BUY"])==1 and self.first_buy_status=="NO_FILL":
            return {"order_id":"early-no-fill","status":"NO_FILL","fill_count":"0.00"}
        qty=Decimal(self.fill)
        self.position+=qty if side=="UP" else -qty
        return {"order_id":"buy-filled","status":"FILLED","fill_count":str(qty)}

@pytest.fixture
def env(tmp_path):
    clock=Clock();feed=Feed(clock);manual=Trader()
    engine=ArmedAutoTrader(manual,feed,now_ns=clock.now,history_path=tmp_path/"history.sqlite3")
    return clock,feed,manual,engine

@pytest.mark.parametrize("early,final,expected_sell,expected_buy",[
    ("ABOVE","BELOW","UP","DOWN"),
    ("BELOW","ABOVE","DOWN","UP")])
def test_opposite_final_sells_early_fill_then_buys_final(env,early,final,expected_sell,expected_buy):
    _,feed,manual,engine=env
    engine.arm("1.50",continuous=True,start_worker=False)
    feed.early();feed.side=early;engine.tick()
    assert len(manual.orders)==1
    assert manual.orders[0][0:2]==("BUY",expected_sell)
    feed.lock(final)
    engine.tick()
    assert [(o[0],o[1]) for o in manual.orders]==[
        ("BUY",expected_sell),("SELL",expected_sell),("BUY",expected_buy)]
    assert manual.quoted[1][2:] == (expected_sell,"SHARES","0.40")
    assert manual.quoted[2][2:] == (expected_buy,"DOLLARS","1.50")
    assert engine.status()["state"]=="WAITING"
    engine.tick()
    assert len(manual.orders)==3

def test_same_direction_final_never_sells_or_rebuys(env):
    _,feed,manual,engine=env
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.early();engine.tick()
    feed.lock("ABOVE");engine.tick()
    assert manual.orders==[("BUY","UP","0.75")]

def test_early_no_fill_still_attempts_final_buy(env):
    _,feed,manual,engine=env
    manual.first_buy_status="NO_FILL"
    engine.arm("0.75",continuous=True,start_worker=False)
    feed.early();engine.tick()
    assert engine.status()["last_result"]=="NO_FILL"
    feed.lock("BELOW");engine.tick()
    assert manual.orders==[("BUY","UP","0.75"),("BUY","DOWN","0.75")]
    assert engine.status()["state"]=="WAITING"

@pytest.mark.parametrize("sell_status",["NO_FILL","PARTIAL"])
def test_failed_or_partial_sell_must_never_buy_opposite(env,sell_status):
    _,feed,manual,engine=env
    manual.sell_status=sell_status
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.early();engine.tick()
    feed.lock("BELOW");engine.tick()
    assert [o[0] for o in manual.orders]==["BUY","SELL"]
    assert engine.status()["state"]!="WAITING" or engine.status().get("requires_review")

def test_cancel_after_early_fill_prevents_reversal(env):
    _,feed,manual,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.early();engine.tick()
    engine.cancel()
    feed.lock("BELOW");engine.tick()
    assert len(manual.orders)==1

def test_skipped_final_leaves_early_position_unchanged(env):
    _,feed,manual,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.early();engine.tick()
    feed.lock("SKIP");engine.tick()
    assert len(manual.orders)==1

def test_history_has_staged_early_sell_final_records(env):
    _,feed,manual,engine=env
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.early();engine.tick()
    feed.lock("BELOW");engine.tick()
    stages=engine.history_store.recent_stages("KXBTC15M-REVERSE1")
    assert [a["stage"] for a in stages]==["EARLY_BUY","REVERSAL_SELL","FINAL_BUY"]
    assert [a["status"] for a in stages]==["FILLED","FILLED","FILLED"]

def test_no_fill_diagnostics_include_quote_and_order_data(env):
    _,feed,manual,engine=env
    manual.first_buy_status="NO_FILL"
    engine.arm("1.00",continuous=True,start_worker=False)
    feed.early();engine.tick()
    row=engine.history()["entries"][0]
    assert row["status"]=="NO_FILL"
    assert row["order_id"]=="early-no-fill"
    assert "0.00"==row["fill_count"]
