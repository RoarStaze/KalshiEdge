from __future__ import annotations

"""Read-only Binance BTCUSDT 1-second kline feed for bootstrap inference."""

import asyncio
import inspect
import json
import math
from collections.abc import Awaitable, Callable

import websockets

from .binance_history import BinanceBar, normalize_epoch_to_ns


BINANCE_STREAM_ORIGIN = "wss://stream.binance.com:9443"


class LiveBinanceProtocolError(RuntimeError):
    """Raised when a required live Binance field is malformed or inconsistent."""


def _finite_float(value: object, field: str, *, nonnegative: bool = False, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveBinanceProtocolError(f"invalid Binance {field}") from exc
    if not math.isfinite(result):
        raise LiveBinanceProtocolError(f"invalid Binance {field}")
    if positive and result <= 0.0:
        raise LiveBinanceProtocolError(f"Binance {field} must be positive")
    if nonnegative and result < 0.0:
        raise LiveBinanceProtocolError(f"Binance {field} must be nonnegative")
    return result


def parse_kline_message(payload: dict, *, expected_symbol: str) -> BinanceBar | None:
    """Parse one official closed UTC 1-second spot kline.

    Open/in-progress klines are deliberately ignored because their OHLC/volume fields
    are not yet causally final. Historical bootstrap features use finalized 1-second
    bars, so live inference consumes only messages with ``x=true``.
    """
    if not isinstance(payload, dict) or payload.get("e") != "kline":
        raise LiveBinanceProtocolError("expected Binance kline event")
    symbol = str(payload.get("s", "")).upper()
    expected = expected_symbol.upper()
    if symbol != expected:
        raise LiveBinanceProtocolError("Binance symbol does not match configured symbol")
    kline = payload.get("k")
    if not isinstance(kline, dict):
        raise LiveBinanceProtocolError("Binance kline payload is missing")
    if str(kline.get("s", symbol)).upper() != expected:
        raise LiveBinanceProtocolError("Binance nested kline symbol mismatch")
    if kline.get("i") != "1s":
        raise LiveBinanceProtocolError("Binance live feature parity requires 1s klines")
    if kline.get("x") is not True:
        return None
    try:
        ts_ns = normalize_epoch_to_ns(int(kline["t"]))
        trade_count = int(kline["n"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveBinanceProtocolError("invalid Binance kline timestamp/count") from exc
    if trade_count < 0:
        raise LiveBinanceProtocolError("Binance trade count must be nonnegative")

    open_price = _finite_float(kline.get("o"), "open", positive=True)
    high = _finite_float(kline.get("h"), "high", positive=True)
    low = _finite_float(kline.get("l"), "low", positive=True)
    close = _finite_float(kline.get("c"), "close", positive=True)
    if high < max(open_price, close, low) or low > min(open_price, close, high):
        raise LiveBinanceProtocolError("Binance kline OHLC is internally inconsistent")
    return BinanceBar(
        ts_ns=ts_ns,
        open=open_price,
        high=high,
        low=low,
        close=close,
        base_volume=_finite_float(kline.get("v"), "base volume", nonnegative=True),
        quote_volume=_finite_float(kline.get("q"), "quote volume", nonnegative=True),
        trade_count=trade_count,
        taker_buy_base=_finite_float(kline.get("V"), "taker buy base", nonnegative=True),
        taker_buy_quote=_finite_float(kline.get("Q"), "taker buy quote", nonnegative=True),
    )


async def _dispatch(callback: Callable[[BinanceBar], object], bar: BinanceBar) -> None:
    result = callback(bar)
    if inspect.isawaitable(result):
        await result


class BinanceLiveFeed:
    """Independent public market-data feed; it owns no filesystem or collector state."""

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        normalized = symbol.upper()
        if not normalized or not normalized.isalnum():
            raise ValueError("Binance symbol must be alphanumeric")
        self.symbol = normalized
        self.ws_url = f"{BINANCE_STREAM_ORIGIN}/ws/{normalized.lower()}@kline_1s"

    async def run_forever(self, callback: Callable[[BinanceBar], object]) -> None:
        delay = 0.5
        while True:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=4096,
                ) as ws:
                    delay = 0.5
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, TypeError) as exc:
                            raise LiveBinanceProtocolError("malformed Binance WebSocket frame") from exc
                        bar = parse_kline_message(payload, expected_symbol=self.symbol)
                        if bar is not None:
                            await _dispatch(callback, bar)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 30.0)
