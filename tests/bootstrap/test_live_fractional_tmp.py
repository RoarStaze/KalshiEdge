from __future__ import annotations

from kalshi_edge.bootstrap.binance_history import BinanceBar
from kalshi_edge.bootstrap.live import LiveFeatureState
from kalshi_edge.bootstrap.live_kalshi import BRTIObservation, LiveMarket, LiveQuote, LiveTrade
from kalshi_edge.bootstrap.types import FeatureRow

NS = 1_000_000_000
OPEN = 1_800_000_000_000_000_000
CLOSE = OPEN + 900 * NS


def test_fractional_final_minute_counts_the_current_observable_second() -> None:
    final_start = CLOSE - 60 * NS
    now = final_start + 30 * NS + 700_000_000
    state = LiveFeatureState()
    state.update_market(LiveMarket(ticker="KXBTC15M-TEST", strike=60_000.0, open_ts_ns=OPEN, close_ts_ns=CLOSE))
    state.update_kalshi(LiveQuote(market_ticker="KXBTC15M-TEST", source_ts_ns=now - NS, yes_bid=0.59, yes_ask=0.61))
    state.update_kalshi(LiveTrade(market_ticker="KXBTC15M-TEST", source_ts_ns=now - NS, yes_price=0.60, count=1.0, taker_side="yes"))
    for ts_ns in range(now - 120 * NS, now, NS):
        state.update_binance(BinanceBar(ts_ns=ts_ns, open=60_000.0, high=60_001.0, low=59_999.0, close=60_000.0, base_volume=1.0, quote_volume=60_000.0, trade_count=1, taker_buy_base=0.5, taker_buy_quote=30_000.0))
    for second in range(31):
        state.update_brti(BRTIObservation(source_ts_ns=final_start + second * NS + 100_000_000, value=60_000.0 + second))
    row = FeatureRow(
        market_ticker="KXBTC15M-TEST",
        market_date="2026-08-31",
        split_group_id="KXBTC15M-TEST",
        checkpoint_ts_ns=now,
        label_yes=0,
        features={"btc_realized_vol_60s": 0.001},
        source_max_ts_ns={"binance": now - NS},
    )

    final = state.final_minute_state(now, row)
    assert final.elapsed_observations == 31
    assert [item.second_index for item in final.observations] == list(range(31))
