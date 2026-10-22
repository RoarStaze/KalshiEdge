from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.binance_history import BinanceBar, parse_spot_1s
from kalshi_edge.bootstrap.features import HistoricalBTCState, HistoricalKalshiState, build_feature_row
from kalshi_edge.bootstrap.live_binance import parse_kline_message
from kalshi_edge.bootstrap.types import MarketLabel


NS = 1_000_000_000


def _bar(open_ts_ns: int, price: float) -> BinanceBar:
    return BinanceBar(
        ts_ns=open_ts_ns,
        open=price,
        high=price,
        low=price,
        close=price,
        base_volume=1.0,
        quote_volume=price,
        trade_count=1,
        taker_buy_base=0.5,
        taker_buy_quote=price / 2,
    )


def _label() -> MarketLabel:
    opening = int(datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp() * NS)
    return MarketLabel(
        ticker="KXBTC15M-CAUSALITY",
        strike=100.0,
        strike_type="greater",
        yes_is_above=True,
        result="yes",
        settlement_value=101.0,
        open_ts_ns=opening,
        close_ts_ns=opening + 900 * NS,
    )


def test_finished_ohlcv_is_unavailable_during_its_own_second() -> None:
    label = _label()
    checkpoint = label.open_ts_ns + 600 * NS
    history = HistoricalBTCState(
        bars=(
            _bar(checkpoint - 2 * NS, 100.0),
            _bar(checkpoint - NS, 101.0),
            _bar(checkpoint, 9000.0),
        )
    )

    before = build_feature_row(label, checkpoint - 1, HistoricalKalshiState(), history)
    at = build_feature_row(label, checkpoint, HistoricalKalshiState(), history)
    just_after = build_feature_row(label, checkpoint + 1, HistoricalKalshiState(), history)
    completed = build_feature_row(label, checkpoint + NS, HistoricalKalshiState(), history)

    assert before.features["btc_close"] == 100.0
    assert at.features["btc_close"] == 101.0
    assert just_after.features["btc_close"] == 101.0
    assert at.features["btc_base_volume_15s"] == 2.0
    assert at.source_max_ts_ns["binance"] == checkpoint
    assert completed.features["btc_close"] == 9000.0
    assert completed.source_max_ts_ns["binance"] == checkpoint + NS


@pytest.mark.parametrize("timestamp_multiplier,close_offset", [(1, 999), (1000, 999_999)])
def test_archive_and_live_candles_share_open_and_completion_boundaries(
    tmp_path: Path, timestamp_multiplier: int, close_offset: int
) -> None:
    opening_ms = 1_800_000_000_000
    opening = opening_ms * timestamp_multiplier
    close_time = opening + close_offset
    file = tmp_path / "BTCUSDT-1s.csv"
    file.write_text(
        f"{opening},60000,60002,59999,60001,1.5,{close_time},90000,4,0.8,48000,0\n",
        encoding="utf-8",
    )
    archived = list(parse_spot_1s(file))[0]
    message = {
        "e": "kline",
        "s": "BTCUSDT",
        "k": {
            "t": opening_ms,
            "T": opening_ms + 999,
            "s": "BTCUSDT",
            "i": "1s",
            "o": "60000",
            "h": "60002",
            "l": "59999",
            "c": "60001",
            "v": "1.5",
            "q": "90000",
            "n": 4,
            "V": "0.8",
            "Q": "48000",
            "x": True,
        },
    }
    live = parse_kline_message(message, expected_symbol="BTCUSDT")
    assert live is not None
    assert archived.ts_ns == live.ts_ns == opening_ms * 1_000_000
    assert archived.completed_ts_ns == live.completed_ts_ns == opening_ms * 1_000_000 + NS
    assert archived.close == live.close == 60001.0
    message["k"]["x"] = False
    assert parse_kline_message(message, expected_symbol="BTCUSDT") is None
