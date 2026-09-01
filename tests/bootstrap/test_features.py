from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_edge.bootstrap.binance_history import BinanceBar
from kalshi_edge.bootstrap.config import DEFAULT_CHECKPOINT_SECONDS
from kalshi_edge.bootstrap.features import (
    HistoricalBTCState,
    HistoricalKalshiCandle,
    HistoricalKalshiState,
    HistoricalKalshiTrade,
    build_feature_row,
    build_market_feature_rows,
)
from kalshi_edge.bootstrap.types import MarketLabel


NS = 1_000_000_000


def _label() -> MarketLabel:
    open_ts = int(datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc).timestamp() * NS)
    return MarketLabel(
        ticker="KXBTC15M-TEST",
        event_ticker="KXBTC15M-EVENT",
        strike=100.0,
        strike_type="greater",
        yes_is_above=True,
        result="yes",
        settlement_value=101.0,
        open_ts_ns=open_ts,
        close_ts_ns=open_ts + 900 * NS,
        settlement_ts_ns=open_ts + 960 * NS,
    )


def _bar(ts_ns: int, close: float, *, high: float | None = None, low: float | None = None) -> BinanceBar:
    return BinanceBar(
        ts_ns=ts_ns,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        base_volume=10.0,
        quote_volume=1000.0,
        trade_count=5,
        taker_buy_base=6.0,
        taker_buy_quote=600.0,
    )


def test_build_feature_row_excludes_every_observation_after_checkpoint() -> None:
    label = _label()
    checkpoint = label.open_ts_ns + 600 * NS
    btc = HistoricalBTCState(
        bars=(
            _bar(checkpoint - 60 * NS, 100.0),
            _bar(checkpoint - 10 * NS, 105.0),
            _bar(checkpoint, 110.0, high=111.0, low=109.0),
            _bar(checkpoint + 1, 9999.0, high=9999.0, low=9999.0),
        )
    )
    kalshi = HistoricalKalshiState(
        trades=(
            HistoricalKalshiTrade(ts_ns=checkpoint - 30 * NS, yes_price=0.55, count=10.0, taker_side="no"),
            HistoricalKalshiTrade(ts_ns=checkpoint, yes_price=0.60, count=20.0, taker_side="yes"),
            HistoricalKalshiTrade(ts_ns=checkpoint + 1, yes_price=0.99, count=999.0, taker_side="yes"),
        ),
        candles=(
            HistoricalKalshiCandle(
                end_ts_ns=checkpoint - 1,
                yes_bid_close=0.56,
                yes_ask_close=0.58,
                price_close=0.57,
                price_high=0.62,
                price_low=0.50,
                volume=100.0,
                open_interest=50.0,
            ),
            HistoricalKalshiCandle(
                end_ts_ns=checkpoint + 1,
                yes_bid_close=0.90,
                yes_ask_close=0.99,
                price_close=0.95,
                price_high=0.99,
                price_low=0.90,
                volume=9999.0,
                open_interest=9999.0,
            ),
        ),
    )

    row = build_feature_row(label, checkpoint, kalshi, btc)

    assert row.market_ticker == label.ticker
    assert row.market_date == "2026-08-01"
    assert row.split_group_id == label.ticker
    assert row.features["btc_close"] == 110.0
    assert row.features["btc_distance"] == 10.0
    assert row.features["btc_return_60s"] == pytest.approx(0.10)
    assert row.features["kalshi_last_trade_yes"] == 0.60
    assert row.features["kalshi_yes_bid"] == 0.56
    assert row.features["kalshi_yes_ask"] == 0.58
    assert row.features["kalshi_candle_price_high"] == 0.62
    assert row.features["kalshi_candle_price_low"] == 0.50
    assert max(row.source_max_ts_ns.values()) <= checkpoint


def test_build_feature_row_uses_only_past_values_for_rolling_statistics() -> None:
    label = _label()
    checkpoint = label.open_ts_ns + 300 * NS
    bars = tuple(_bar(checkpoint - offset * NS, 100.0 + (60 - offset) * 0.1) for offset in range(60, -1, -1))
    btc = HistoricalBTCState(bars=bars + (_bar(checkpoint + 1, 10000.0),))
    kalshi = HistoricalKalshiState(trades=(), candles=())

    row = build_feature_row(label, checkpoint, kalshi, btc)

    assert row.features["btc_close"] == pytest.approx(106.0)
    assert row.features["btc_return_60s"] == pytest.approx(0.06)
    assert row.features["btc_realized_vol_60s"] >= 0.0
    assert row.source_max_ts_ns["binance"] == checkpoint


def test_market_builder_generates_all_checkpoints_in_one_split_group() -> None:
    label = _label()
    bars = tuple(_bar(label.open_ts_ns + second * NS, 100.0 + second / 1000.0) for second in range(0, 901))
    rows = build_market_feature_rows(
        label,
        HistoricalKalshiState(),
        HistoricalBTCState(bars=bars),
        checkpoint_seconds=DEFAULT_CHECKPOINT_SECONDS,
    )

    assert len(rows) == len(DEFAULT_CHECKPOINT_SECONDS) == 18
    assert {row.market_ticker for row in rows} == {label.ticker}
    assert {row.market_date for row in rows} == {"2026-08-01"}
    assert {row.split_group_id for row in rows} == {label.ticker}
    assert {row.label_yes for row in rows} == {1}
    assert [row.features["seconds_remaining"] for row in rows] == [float(value) for value in DEFAULT_CHECKPOINT_SECONDS]
    assert len({row.checkpoint_ts_ns for row in rows}) == 18
