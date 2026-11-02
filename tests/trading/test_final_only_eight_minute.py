"""New policy: immutable 8-minute final is the only AutoTrade BUY signal.

The tests never call real Kalshi endpoints.
"""
from kalshi_edge.bootstrap.final_forecast import (
    FINAL_CHECKPOINT_SECONDS, FinalForecastGate, FinalForecastRecord, FinalForecastWriter,
)
from kalshi_edge.bootstrap.live_dashboard import read_live_dashboard
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.live_kalshi import LiveMarket
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader

NS = 1_000_000_000
OPEN = 1_900_000_000 * NS
CLOSE = OPEN + 900 * NS
DUE = CLOSE - 480 * NS
TICKER = "KXBTC15M-EIGHTMIN"


def prediction(at):
    return LivePredictionRecord(
        prediction_ts_ns=at, market_ticker=TICKER, status="OK",
        p_yes=.94, p_no=.06, predicted_side="ABOVE", confidence=.88,
        feed_quality=FeedQuality(healthy=True), model_hash="test",
    )


def test_final_locks_only_at_eight_minutes_and_archives_checkpoint_metadata(tmp_path):
    assert FINAL_CHECKPOINT_SECONDS == 480
    market = LiveMarket(ticker=TICKER, strike=70000.0, open_ts_ns=OPEN, close_ts_ns=CLOSE)
    gate = FinalForecastGate()
    assert gate.evaluate(market, now_ns=DUE - 1, prediction=prediction(DUE-1)) is None
    final = gate.evaluate(market, now_ns=DUE, prediction=prediction(DUE))
    assert final.forecast == "ABOVE"
    assert final.scheduled_ts_ns == DUE
    assert final.seconds_remaining == 480
    assert final.checkpoint_seconds == 480
    saved = FinalForecastWriter(tmp_path).save(final, market_close_ts_ns=CLOSE)
    assert saved is not None
    assert FinalForecastRecord.model_validate_json(saved.read_text()).checkpoint_seconds == 480


def test_old_four_minute_forecast_keeps_correct_close_time(tmp_path):
    from kalshi_edge.bootstrap.final_history import _close_ns
    legacy = FinalForecastRecord(
        market_ticker=TICKER, strike=70000.0, scheduled_ts_ns=CLOSE-240*NS,
        prediction_ts_ns=CLOSE-240*NS, seconds_remaining=240,
        forecast="ABOVE", p_yes=.90, p_no=.10,
    )
    raw = legacy.model_dump()
    raw.pop("checkpoint_seconds")
    recovered = FinalForecastRecord.model_validate(raw)
    assert recovered.checkpoint_seconds == 240
    assert _close_ns(recovered) == CLOSE
    assert FinalForecastWriter(tmp_path).save(recovered, market_close_ts_ns=CLOSE)


class FakeExchange:
    def __init__(self):
        self.previews=[]
        self.orders=[]
    def preview(self,ticker,action,outcome,units,amount):
        self.previews.append((ticker,action,outcome,units,amount))
        return {
            "ticker":ticker, "action":action, "outcome":outcome,
            "preview_id":"snapshot", "count":"0.25", "unit_price":"0.4000",
            "fee_estimate":"0.005000", "amount_estimate":"0.1050",
        }
    def place(self, preview_id, ticker):
        self.orders.append((preview_id,ticker))
        return {"status":"FILLED", "fill_count":"0.25","order_id":"once"}


def test_autotrade_never_uses_live_confidence_before_locked_final(tmp_path):
    clock = [OPEN]
    feed = {
        "market_ticker":TICKER, "market_close_ts_ns":CLOSE,
        "checkpoint_ts_ns":DUE, "status":"LIVE", "final_forecast":None,
        "latest_prediction":{
            "market_ticker":TICKER, "prediction_ts_ns":OPEN,
            "status":"OK", "predicted_side":"ABOVE", "confidence":1.0,
        },
    }
    exchange=FakeExchange()
    trader=ArmedAutoTrader(exchange, lambda:feed, now_ns=lambda:clock[0],
                           history_path=tmp_path/"journal.sqlite3")
    trader.arm("0.25", continuous=True, start_worker=False)
    for remain in [750, 600, 500, 481]:
        clock[0] = CLOSE-remain*NS
        feed["latest_prediction"]["prediction_ts_ns"]=clock[0]
        trader.tick()
    assert exchange.orders == []
    assert exchange.previews == []
    clock[0]=DUE + NS
    feed["final_forecast"]={
        "market_ticker":TICKER, "scheduled_ts_ns":DUE, "forecast":"BELOW",
        "confidence":.89, "checkpoint_seconds":480,
    }
    trader.tick()
    assert exchange.previews == [(TICKER,"BUY","DOWN","DOLLARS","0.25")]
    assert len(exchange.orders)==1
    assert [s["stage"] for s in trader.history_store.recent_stages(TICKER)]==["FINAL_BUY"]
    for _ in range(5):
        trader.tick()
    assert len(exchange.orders)==1


def test_old_four_minute_final_is_never_accepted_for_new_eight_minute_policy(tmp_path):
    clock=[OPEN]
    feed={
        "market_ticker":TICKER,"market_close_ts_ns":CLOSE,
        "checkpoint_ts_ns":DUE,"status":"LIVE",
        "final_forecast":None,
    }
    exchange=FakeExchange()
    trader=ArmedAutoTrader(exchange,lambda:feed,now_ns=lambda:clock[0],
                           history_path=tmp_path/"journal.sqlite3")
    trader.arm("0.25",start_worker=False)
    clock[0]=DUE+NS
    feed["final_forecast"]={
        "market_ticker":TICKER,"scheduled_ts_ns":CLOSE-240*NS,
        "forecast":"ABOVE","confidence":.98,
    }
    trader.tick()
    assert exchange.orders==[]
    assert exchange.previews==[]


def test_no_reversal_or_early_stage_code_is_reachable_by_final_only_engine():
    import inspect
    source=inspect.getsource(ArmedAutoTrader._tick)
    assert "REVERSAL_SELL" not in source
    assert "EARLY_BUY" not in source
    assert "_early_signal(" not in source
    assert '"FINAL_BUY"' in source
