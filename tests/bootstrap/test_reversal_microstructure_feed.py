"""Market-microstructure observations must use actual Kalshi book and trades."""
from pathlib import Path

from kalshi_edge.config import CollectorSettings
from kalshi_edge.bootstrap.live import LiveFeatureState
from kalshi_edge.bootstrap.live_kalshi import KalshiLiveFeed, LiveMarket, LiveTrade

NS = 1_000_000_000
START = 1_800_000_000 * NS
TICKER = "KXBTC15M-TEST"


def test_orderbook_reports_exact_best_level_quantity_after_deltas():
    feed = KalshiLiveFeed(CollectorSettings(
        key_id="test", private_key_path=Path("test.pem")))
    quote, = feed.process_message({
        "type": "orderbook_snapshot",
        "msg": {
            "market_ticker": TICKER,
            "yes_dollars_fp": [["0.4100", "10.00"], ["0.4000", "100.00"]],
            "no_dollars_fp": [["0.5900", "8.00"], ["0.6000", "50.00"]],
        },
    }, receive_ts_ns=START)
    assert quote.yes_bid_depth == 10.0
    assert quote.yes_ask_depth == 8.0
    quote, = feed.process_message({
        "type": "orderbook_delta",
        "msg": {"market_ticker": TICKER, "side": "yes",
                "price_dollars": "0.4100", "delta_fp": "-3.00"},
    }, receive_ts_ns=START + NS)
    assert quote.yes_bid_depth == 7.0
    assert quote.yes_ask_depth == 8.0


def test_taker_flow_is_time_bounded_and_does_not_use_future_events():
    state = LiveFeatureState()
    state.update_market(LiveMarket(
        ticker=TICKER,strike=64000,open_ts_ns=START-400*NS,
        close_ts_ns=START+900*NS,
    ))
    for offset, side, volume in [
        (-40,"yes",100.),(-8,"yes",3.),(-4,"no",5.),(-1,"no",2.),(3,"yes",99.)
    ]:
        state.update_kalshi(LiveTrade(
            market_ticker=TICKER,source_ts_ns=START+offset*NS,
            yes_price=.5,count=volume,taker_side=side))
    signed,total=state.recent_taker_flow(START, seconds=10)
    assert signed == -4.0
    assert total == 10.0
    assert state.recent_taker_flow(START, seconds=5) == (-7.0, 7.0)


def test_empty_taker_window_returns_zero_not_invented_pressure():
    state=LiveFeatureState()
    assert state.recent_taker_flow(START, seconds=10) == (0.0, 0.0)


def test_stream_epoch_advances_for_reconnection_on_same_market():
    state=LiveFeatureState()
    m=LiveMarket(ticker=TICKER,strike=64000,
                 open_ts_ns=START-400*NS,close_ts_ns=START+900*NS)
    first=state.market_stream_epoch
    state.update_market(m)
    second=state.market_stream_epoch
    state.update_market(m)
    assert second == first+1
    assert state.market_stream_epoch == second+1
