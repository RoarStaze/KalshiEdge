from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .binance_history import BinanceBar
from .types import FeatureRow, MarketLabel


NS = 1_000_000_000
_RETURN_HORIZONS = (5, 10, 15, 30, 60, 120, 300, 600)
_VOL_HORIZONS = (15, 30, 60, 180, 300, 900)
_FLOW_HORIZONS = (15, 30, 60, 300)


class FeatureConstructionError(RuntimeError):
    pass


class HistoricalKalshiTrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts_ns: int
    yes_price: float = Field(ge=0.0, le=1.0)
    count: float = Field(ge=0.0)
    taker_side: Literal["yes", "no"]


class HistoricalKalshiCandle(BaseModel):
    model_config = ConfigDict(frozen=True)

    end_ts_ns: int
    yes_bid_close: float | None = Field(default=None, ge=0.0, le=1.0)
    yes_ask_close: float | None = Field(default=None, ge=0.0, le=1.0)
    price_close: float | None = Field(default=None, ge=0.0, le=1.0)
    price_high: float | None = Field(default=None, ge=0.0, le=1.0)
    price_low: float | None = Field(default=None, ge=0.0, le=1.0)
    volume: float = Field(default=0.0, ge=0.0)
    open_interest: float = Field(default=0.0, ge=0.0)


class HistoricalKalshiState(BaseModel):
    model_config = ConfigDict(frozen=True)

    trades: tuple[HistoricalKalshiTrade, ...] = ()
    candles: tuple[HistoricalKalshiCandle, ...] = ()


class HistoricalBTCState(BaseModel):
    model_config = ConfigDict(frozen=True)

    bars: tuple[BinanceBar, ...]


def _sorted_past_bars(bars: Sequence[BinanceBar], checkpoint_ts_ns: int) -> list[BinanceBar]:
    return sorted((bar for bar in bars if bar.ts_ns <= checkpoint_ts_ns), key=lambda bar: bar.ts_ns)


def _bar_at_or_before(bars: Sequence[BinanceBar], ts_ns: int) -> BinanceBar | None:
    candidate: BinanceBar | None = None
    for bar in bars:
        if bar.ts_ns <= ts_ns and (candidate is None or bar.ts_ns > candidate.ts_ns):
            candidate = bar
    return candidate


def _realized_volatility(bars: Sequence[BinanceBar], start_ts_ns: int, checkpoint_ts_ns: int) -> float:
    selected = [bar for bar in bars if start_ts_ns <= bar.ts_ns <= checkpoint_ts_ns and bar.close > 0]
    selected.sort(key=lambda bar: bar.ts_ns)
    if len(selected) < 2:
        return 0.0
    returns = [math.log(current.close / previous.close) for previous, current in zip(selected, selected[1:]) if previous.close > 0]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return math.sqrt(max(0.0, variance))


def _window_bars(bars: Sequence[BinanceBar], checkpoint_ts_ns: int, seconds: int) -> list[BinanceBar]:
    lower = checkpoint_ts_ns - seconds * NS
    return [bar for bar in bars if lower <= bar.ts_ns <= checkpoint_ts_ns]


def _window_trades(trades: Sequence[HistoricalKalshiTrade], checkpoint_ts_ns: int, seconds: int) -> list[HistoricalKalshiTrade]:
    lower = checkpoint_ts_ns - seconds * NS
    return [trade for trade in trades if lower <= trade.ts_ns <= checkpoint_ts_ns]


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if abs(denominator) > 1e-15 else 0.0


def build_feature_row(
    label: MarketLabel,
    checkpoint_ts_ns: int,
    kalshi: HistoricalKalshiState,
    btc: HistoricalBTCState,
) -> FeatureRow:
    if checkpoint_ts_ns < label.open_ts_ns or checkpoint_ts_ns > label.close_ts_ns:
        raise FeatureConstructionError("checkpoint must be inside the market observation window")

    past_bars = _sorted_past_bars(btc.bars, checkpoint_ts_ns)
    if not past_bars:
        raise FeatureConstructionError("no Binance observation is available at or before checkpoint")
    current = past_bars[-1]

    past_trades = sorted((trade for trade in kalshi.trades if trade.ts_ns <= checkpoint_ts_ns), key=lambda item: item.ts_ns)
    completed_candles = sorted(
        (candle for candle in kalshi.candles if candle.end_ts_ns <= checkpoint_ts_ns),
        key=lambda item: item.end_ts_ns,
    )

    seconds_remaining = max(0.0, (label.close_ts_ns - checkpoint_ts_ns) / NS)
    duration_ns = max(1, label.close_ts_ns - label.open_ts_ns)
    elapsed_fraction = min(1.0, max(0.0, (checkpoint_ts_ns - label.open_ts_ns) / duration_ns))
    distance = current.close - label.strike
    features: dict[str, float] = {
        "seconds_remaining": seconds_remaining,
        "elapsed_fraction": elapsed_fraction,
        "strike": label.strike,
        "btc_close": current.close,
        "btc_distance": distance,
        "btc_distance_bps": _safe_ratio(distance * 10_000.0, label.strike),
    }

    for horizon in _RETURN_HORIZONS:
        anchor = _bar_at_or_before(past_bars, checkpoint_ts_ns - horizon * NS)
        key = f"btc_return_{horizon}s"
        available_key = f"btc_return_{horizon}s_available"
        if anchor is None or anchor.close <= 0:
            features[key] = 0.0
            features[available_key] = 0.0
        else:
            features[key] = current.close / anchor.close - 1.0
            features[available_key] = 1.0

    for horizon in _VOL_HORIZONS:
        features[f"btc_realized_vol_{horizon}s"] = _realized_volatility(
            past_bars,
            checkpoint_ts_ns - horizon * NS,
            checkpoint_ts_ns,
        )

    market_bars = [bar for bar in past_bars if bar.ts_ns >= label.open_ts_ns]
    if market_bars:
        high = max(bar.high for bar in market_bars)
        low = min(bar.low for bar in market_bars)
        features["btc_market_high_excursion_bps"] = _safe_ratio((high - label.strike) * 10_000.0, label.strike)
        features["btc_market_low_excursion_bps"] = _safe_ratio((low - label.strike) * 10_000.0, label.strike)
    else:
        features["btc_market_high_excursion_bps"] = 0.0
        features["btc_market_low_excursion_bps"] = 0.0

    ten = _bar_at_or_before(past_bars, checkpoint_ts_ns - 10 * NS)
    twenty = _bar_at_or_before(past_bars, checkpoint_ts_ns - 20 * NS)
    features["btc_distance_velocity_10s"] = (current.close - ten.close) / 10.0 if ten else 0.0
    features["btc_distance_acceleration_10s"] = (
        (current.close - 2.0 * ten.close + twenty.close) / 100.0 if ten and twenty else 0.0
    )

    for horizon in _FLOW_HORIZONS:
        window = _window_bars(past_bars, checkpoint_ts_ns, horizon)
        volume = sum(bar.base_volume for bar in window)
        buy_volume = sum(bar.taker_buy_base for bar in window)
        features[f"btc_base_volume_{horizon}s"] = volume
        features[f"btc_trade_count_{horizon}s"] = float(sum(bar.trade_count for bar in window))
        features[f"btc_taker_buy_imbalance_{horizon}s"] = _safe_ratio(2.0 * buy_volume - volume, volume)

    rv60 = features["btc_realized_vol_60s"]
    scale = current.close * rv60 * math.sqrt(max(1.0, seconds_remaining))
    features["btc_normalized_distance"] = _safe_ratio(distance, scale)

    last_trade = past_trades[-1] if past_trades else None
    features["kalshi_trade_available"] = 1.0 if last_trade else 0.0
    features["kalshi_last_trade_yes"] = last_trade.yes_price if last_trade else 0.0
    features["kalshi_trade_staleness_seconds"] = (
        max(0.0, (checkpoint_ts_ns - last_trade.ts_ns) / NS) if last_trade else float((checkpoint_ts_ns - label.open_ts_ns) / NS)
    )

    for horizon in _FLOW_HORIZONS:
        trades = _window_trades(past_trades, checkpoint_ts_ns, horizon)
        total = sum(trade.count for trade in trades)
        yes_taker = sum(trade.count for trade in trades if trade.taker_side == "yes")
        features[f"kalshi_trade_volume_{horizon}s"] = total
        features[f"kalshi_yes_taker_imbalance_{horizon}s"] = _safe_ratio(2.0 * yes_taker - total, total)
        if len(trades) >= 2:
            features[f"kalshi_trade_return_{horizon}s"] = trades[-1].yes_price - trades[0].yes_price
        else:
            features[f"kalshi_trade_return_{horizon}s"] = 0.0

    last_candle = completed_candles[-1] if completed_candles else None
    features["kalshi_quote_available"] = 1.0 if last_candle else 0.0
    if last_candle:
        bid = last_candle.yes_bid_close
        ask = last_candle.yes_ask_close
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else (last_candle.price_close or 0.0)
        features.update(
            {
                "kalshi_yes_bid": bid if bid is not None else 0.0,
                "kalshi_yes_ask": ask if ask is not None else 0.0,
                "kalshi_mid": mid,
                "kalshi_spread": (ask - bid) if bid is not None and ask is not None else 0.0,
                "kalshi_candle_price_close": last_candle.price_close or 0.0,
                "kalshi_candle_price_high": last_candle.price_high or 0.0,
                "kalshi_candle_price_low": last_candle.price_low or 0.0,
                "kalshi_candle_volume": last_candle.volume,
                "kalshi_open_interest": last_candle.open_interest,
                "kalshi_quote_staleness_seconds": max(0.0, (checkpoint_ts_ns - last_candle.end_ts_ns) / NS),
                "kalshi_mid_minus_half": mid - 0.5,
            }
        )
    else:
        for name in (
            "kalshi_yes_bid",
            "kalshi_yes_ask",
            "kalshi_mid",
            "kalshi_spread",
            "kalshi_candle_price_close",
            "kalshi_candle_price_high",
            "kalshi_candle_price_low",
            "kalshi_candle_volume",
            "kalshi_open_interest",
            "kalshi_quote_staleness_seconds",
            "kalshi_mid_minus_half",
        ):
            features[name] = 0.0

    source_max: dict[str, int] = {"binance": current.ts_ns}
    if last_trade:
        source_max["kalshi_trades"] = last_trade.ts_ns
    if last_candle:
        source_max["kalshi_candles"] = last_candle.end_ts_ns

    market_date = datetime.fromtimestamp(label.open_ts_ns / NS, tz=timezone.utc).date().isoformat()
    return FeatureRow(
        market_ticker=label.ticker,
        market_date=market_date,
        split_group_id=label.ticker,
        checkpoint_ts_ns=checkpoint_ts_ns,
        label_yes=1 if label.result == "yes" else 0,
        features=features,
        source_max_ts_ns=source_max,
    )


def build_market_feature_rows(
    label: MarketLabel,
    kalshi: HistoricalKalshiState,
    btc: HistoricalBTCState,
    *,
    checkpoint_seconds: Sequence[int],
) -> tuple[FeatureRow, ...]:
    duration_seconds = (label.close_ts_ns - label.open_ts_ns) / NS
    seen: set[int] = set()
    rows: list[FeatureRow] = []
    for seconds_remaining in checkpoint_seconds:
        value = int(seconds_remaining)
        if value <= 0 or value >= duration_seconds:
            raise FeatureConstructionError(f"invalid checkpoint seconds remaining: {seconds_remaining}")
        if value in seen:
            raise FeatureConstructionError(f"duplicate checkpoint seconds remaining: {seconds_remaining}")
        seen.add(value)
        checkpoint_ts_ns = label.close_ts_ns - value * NS
        rows.append(build_feature_row(label, checkpoint_ts_ns, kalshi, btc))
    return tuple(rows)
