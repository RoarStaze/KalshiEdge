from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from kalshi_edge.config import CollectorSettings


def test_kalshi_live_feed_has_no_collector_writer_or_order_surface() -> None:
    from kalshi_edge.bootstrap import live_kalshi

    source = inspect.getsource(live_kalshi)
    assert "RawSegmentWriter" not in source
    assert "KalshiCollector" not in source

    feed = live_kalshi.KalshiLiveFeed(
        CollectorSettings(key_id="test-key", private_key_path=Path("test.pem")),
        series_ticker="KXBTC15M",
    )
    forbidden = ("order", "cancel", "portfolio", "position", "fill")
    public_names = [name.lower() for name in dir(feed) if not name.startswith("_")]
    assert not [name for name in public_names if any(token in name for token in forbidden)]


def test_kalshi_subscriptions_use_unified_yes_price_and_read_only_channels() -> None:
    from kalshi_edge.bootstrap.live_kalshi import KalshiLiveFeed

    feed = KalshiLiveFeed(
        CollectorSettings(key_id="test-key", private_key_path=Path("test.pem")),
        series_ticker="KXBTC15M",
    )
    messages = feed.subscription_messages(["KXBTC15M-TEST"])

    orderbook = next(message for message in messages if message["params"]["channels"] == ["orderbook_delta"])
    assert orderbook["params"]["market_tickers"] == ["KXBTC15M-TEST"]
    assert orderbook["params"]["use_yes_price"] is True
    channels = {message["params"]["channels"][0] for message in messages}
    assert channels == {"orderbook_delta", "trade", "cfbenchmarks_value", "market_lifecycle_v2"}


def test_kalshi_feed_parses_market_quote_trade_brti_and_lifecycle() -> None:
    from kalshi_edge.bootstrap.live_kalshi import (
        BRTIObservation,
        KalshiLiveFeed,
        LiveMarket,
        LiveQuote,
        LiveTrade,
        MarketLifecycle,
    )

    feed = KalshiLiveFeed(
        CollectorSettings(key_id="test-key", private_key_path=Path("test.pem")),
        series_ticker="KXBTC15M",
    )

    market = feed.market_from_events_payload(
        {
            "events": [
                {
                    "series_ticker": "KXBTC15M",
                    "markets": [
                        {
                            "ticker": "KXBTC15M-TEST",
                            "status": "active",
                            "floor_strike": 60000.0,
                            "open_time": "2026-08-31T19:00:00Z",
                            "close_time": "2026-08-31T19:15:00Z",
                            "open_interest_fp": "12.50",
                        }
                    ],
                }
            ]
        }
    )
    assert isinstance(market, LiveMarket)
    assert market.ticker == "KXBTC15M-TEST"
    assert market.strike == 60000.0
    assert market.close_ts_ns > market.open_ts_ns

    snapshot = feed.process_message(
        {
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "yes_dollars_fp": [["0.4100", "10.00"]],
                "no_dollars_fp": [["0.5900", "8.00"]],
            },
        },
        receive_ts_ns=1_800_000_000_000_000_000,
    )
    assert len(snapshot) == 1
    assert isinstance(snapshot[0], LiveQuote)
    assert snapshot[0].yes_bid == 0.41
    assert snapshot[0].yes_ask == 0.59

    delta = feed.process_message(
        {
            "type": "orderbook_delta",
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "side": "no",
                "price_dollars": "0.5700",
                "delta_fp": "3.00",
                "ts_ms": 1_800_000_000_100,
            },
        },
        receive_ts_ns=1_800_000_000_200_000_000,
    )
    assert isinstance(delta[0], LiveQuote)
    assert delta[0].yes_ask == 0.57

    trade = feed.process_message(
        {
            "type": "trade",
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "yes_price_dollars": "0.5800",
                "count_fp": "2.50",
                "taker_outcome_side": "yes",
                "ts_ms": 1_800_000_001_000,
            },
        },
        receive_ts_ns=1_800_000_001_100_000_000,
    )
    assert isinstance(trade[0], LiveTrade)
    assert trade[0].yes_price == 0.58
    assert trade[0].count == 2.5
    assert trade[0].taker_side == "yes"

    brti = feed.process_message(
        {
            "type": "cfbenchmarks_value",
            "msg": {
                "index_id": "BRTI",
                "data": json.dumps({"time": 1_800_000_002_000, "value": "60001.25"}),
            },
        },
        receive_ts_ns=1_800_000_002_100_000_000,
    )
    assert isinstance(brti[0], BRTIObservation)
    assert brti[0].value == 60001.25
    assert brti[0].source_ts_ns == 1_800_000_002_000_000_000

    lifecycle = feed.process_message(
        {
            "type": "market_lifecycle_v2",
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "event_type": "metadata_updated",
                "floor_strike": 60005.0,
            },
        },
        receive_ts_ns=1_800_000_003_000_000_000,
    )
    assert isinstance(lifecycle[0], MarketLifecycle)
    assert lifecycle[0].event_type == "metadata_updated"
    assert lifecycle[0].floor_strike == 60005.0


def test_kalshi_feed_rejects_ambiguous_or_malformed_required_values() -> None:
    from kalshi_edge.bootstrap.live_kalshi import LiveFeedProtocolError, KalshiLiveFeed

    feed = KalshiLiveFeed(
        CollectorSettings(key_id="test-key", private_key_path=Path("test.pem")),
        series_ticker="KXBTC15M",
    )
    with pytest.raises(LiveFeedProtocolError):
        feed.process_message(
            {
                "type": "trade",
                "msg": {
                    "market_ticker": "KXBTC15M-TEST",
                    "yes_price_dollars": "not-a-price",
                    "count_fp": "2.50",
                    "taker_outcome_side": "yes",
                },
            },
            receive_ts_ns=1_800_000_001_100_000_000,
        )


def test_binance_live_feed_uses_official_closed_one_second_klines() -> None:
    from kalshi_edge.bootstrap import live_binance

    source = inspect.getsource(live_binance)
    assert "RawSegmentWriter" not in source
    assert "KalshiCollector" not in source

    feed = live_binance.BinanceLiveFeed("BTCUSDT")
    assert feed.ws_url == "wss://stream.binance.com:9443/ws/btcusdt@kline_1s"
    assert not [name for name in dir(feed) if any(token in name.lower() for token in ("order", "cancel", "portfolio"))]

    open_payload = {
        "e": "kline",
        "E": 1_800_000_000_500,
        "s": "BTCUSDT",
        "k": {
            "t": 1_800_000_000_000,
            "T": 1_800_000_000_999,
            "s": "BTCUSDT",
            "i": "1s",
            "o": "60000.0",
            "h": "60002.0",
            "l": "59999.0",
            "c": "60001.0",
            "v": "1.5",
            "q": "90000.0",
            "n": 4,
            "x": False,
            "V": "0.8",
            "Q": "48000.0",
        },
    }
    assert live_binance.parse_kline_message(open_payload, expected_symbol="BTCUSDT") is None

    closed_payload = {**open_payload, "k": {**open_payload["k"], "x": True}}
    bar = live_binance.parse_kline_message(closed_payload, expected_symbol="BTCUSDT")
    assert bar is not None
    assert bar.ts_ns == 1_800_000_000_000_000_000
    assert bar.close == 60001.0
    assert bar.trade_count == 4
    assert bar.taker_buy_base == 0.8


def test_binance_parser_rejects_wrong_symbol_interval_and_bad_prices() -> None:
    from kalshi_edge.bootstrap.live_binance import LiveBinanceProtocolError, parse_kline_message

    base = {
        "e": "kline",
        "E": 1_800_000_000_500,
        "s": "BTCUSDT",
        "k": {
            "t": 1_800_000_000_000,
            "T": 1_800_000_000_999,
            "s": "BTCUSDT",
            "i": "1s",
            "o": "60000.0",
            "h": "60002.0",
            "l": "59999.0",
            "c": "60001.0",
            "v": "1.5",
            "q": "90000.0",
            "n": 4,
            "x": True,
            "V": "0.8",
            "Q": "48000.0",
        },
    }
    with pytest.raises(LiveBinanceProtocolError):
        parse_kline_message({**base, "s": "ETHUSDT"}, expected_symbol="BTCUSDT")
    with pytest.raises(LiveBinanceProtocolError):
        parse_kline_message({**base, "k": {**base["k"], "i": "1m"}}, expected_symbol="BTCUSDT")
    with pytest.raises(LiveBinanceProtocolError):
        parse_kline_message({**base, "k": {**base["k"], "c": "nan"}}, expected_symbol="BTCUSDT")
