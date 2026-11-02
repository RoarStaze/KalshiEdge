"""Historical early-entry scenarios replaced by the final-only strategy.

No live prediction, even confidence=1, may preview or buy before final lock.
"""
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
NS = 1_000_000_000


class Feed:
    def __init__(self):
        self.close = 1_900_000_900 * NS
        self.now = self.close - 900 * NS
        self.ticker = "KXBTC15M-FINAL-ONLY"
        self.final = None
        self.side = "ABOVE"
        self.confidence = 1.0
    def __call__(self):
        return {
            "market_ticker":self.ticker, "market_close_ts_ns":self.close,
            "checkpoint_ts_ns":self.close - 480*NS, "status":"LIVE",
            "latest_prediction":{
                "market_ticker":self.ticker,"prediction_ts_ns":self.now,
                "status":"OK","predicted_side":self.side,
                "confidence":self.confidence,
            },
            "final_forecast":self.final,
        }
    def lock(self,side="ABOVE",confidence=.90):
        self.now=self.close-479*NS
        self.final={"market_ticker":self.ticker,
                    "scheduled_ts_ns":self.close-480*NS,
                    "checkpoint_seconds":480,
                    "forecast":side,"confidence":confidence}


class Exchange:
    def __init__(self):
        self.previews=[];self.orders=[];self.on_preview=None
    def preview(self,ticker,action,side,units,amount):
        self.previews.append((ticker,action,side,units,amount))
        if self.on_preview:self.on_preview()
        return {"ticker":ticker,"action":action,"outcome":side,
                "preview_id":"id","count":"0.25",
                "unit_price":"0.4000","fee_estimate":"0.005000",
                "amount_estimate":"0.1050"}
    def place(self,preview_id,ticker):
        self.orders.append((preview_id,ticker))
        return {"status":"FILLED","fill_count":"0.25","order_id":"one"}


def make(tmp_path):
    feed=Feed();exc=Exchange()
    trader=ArmedAutoTrader(exc,feed,now_ns=lambda:feed.now,
                           history_path=tmp_path/"journal.sqlite3")
    trader.arm("0.25",continuous=True,start_worker=False)
    return feed,exc,trader


def test_extreme_live_confidence_cannot_trigger_before_final(tmp_path):
    feed,exchange,trader=make(tmp_path)
    for seconds_remaining in (899,700,600,500,481):
        feed.now=feed.close-seconds_remaining*NS
        feed.side="ABOVE" if seconds_remaining%2 else "BELOW"
        trader.tick()
    assert not exchange.previews
    assert not exchange.orders
    assert trader.status()["state"]=="WAITING"


def test_eight_minute_final_is_only_buy_signal(tmp_path):
    feed,exchange,trader=make(tmp_path)
    feed.now=feed.close-490*NS
    trader.tick()
    assert not exchange.previews
    feed.lock("BELOW")
    trader.tick()
    assert exchange.previews==[(feed.ticker,"BUY","DOWN","DOLLARS","0.25")]
    assert len(exchange.orders)==1
    assert [s["stage"] for s in trader.history_store.recent_stages(feed.ticker)]==["FINAL_BUY"]


def test_stream_flip_after_final_never_causes_reversal(tmp_path):
    feed,exchange,trader=make(tmp_path)
    feed.lock("ABOVE")
    trader.tick()
    feed.side="BELOW"
    feed.confidence=1.0
    for _ in range(8):trader.tick()
    assert len(exchange.orders)==1
    assert len(exchange.previews)==1


def test_quote_change_between_final_and_submit_cancels_order(tmp_path):
    feed,exchange,trader=make(tmp_path)
    feed.lock("ABOVE")
    exchange.on_preview=lambda: setattr(feed,"final",None)
    trader.tick()
    assert not exchange.orders
    assert trader.status()["state"]=="WAITING"


def test_final_skip_does_not_order_even_with_extreme_continuous_confidence(tmp_path):
    feed,exchange,trader=make(tmp_path)
    feed.lock("SKIP",confidence=.35)
    trader.tick()
    assert not exchange.orders
    assert trader.status()["last_result"]=="SKIPPED"


def test_arming_after_final_lock_requires_next_market(tmp_path):
    feed,exchange,trader=make(tmp_path)
    feed.lock("ABOVE")
    trader.tick()
    assert len(exchange.orders)==1
    feed.now=feed.close-400*NS
    feed.side="BELOW"
    trader.tick()
    assert len(exchange.orders)==1
