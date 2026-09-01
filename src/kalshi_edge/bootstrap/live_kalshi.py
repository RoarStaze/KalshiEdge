from __future__ import annotations

"""Independent read-only Kalshi market/BRTI feed for bootstrap live inference."""

import asyncio
import inspect
import json
import math
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import websockets
from pydantic import BaseModel, ConfigDict, Field

from ..auth import create_auth_headers, load_private_key
from ..config import CollectorSettings
from ..discovery import fetch_open_events_payload


NS = 1_000_000_000


class LiveFeedProtocolError(RuntimeError):
    """Raised when a required Kalshi live datum is malformed or ambiguous."""


class LiveMarket(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    strike: float = Field(gt=0)
    open_ts_ns: int = Field(gt=0)
    close_ts_ns: int = Field(gt=0)
    open_interest: float = Field(default=0.0, ge=0)


class LiveQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_ticker: str
    source_ts_ns: int = Field(gt=0)
    yes_bid: float | None = Field(default=None, ge=0.0, le=1.0)
    yes_ask: float | None = Field(default=None, ge=0.0, le=1.0)


class LiveTrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_ticker: str
    source_ts_ns: int = Field(gt=0)
    yes_price: float = Field(ge=0.0, le=1.0)
    count: float = Field(ge=0.0)
    taker_side: str


class BRTIObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_ts_ns: int = Field(gt=0)
    value: float = Field(gt=0)


class MarketLifecycle(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_ticker: str
    event_type: str
    receive_ts_ns: int = Field(gt=0)
    floor_strike: float | None = Field(default=None, gt=0)


def _finite_float(value: object, field: str, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveFeedProtocolError(f"invalid Kalshi {field}") from exc
    if not math.isfinite(result):
        raise LiveFeedProtocolError(f"invalid Kalshi {field}")
    if minimum is not None and result < minimum:
        raise LiveFeedProtocolError(f"invalid Kalshi {field}")
    return result


def _epoch_to_ns(value: int) -> int:
    if 1_000_000_000_000 <= value < 100_000_000_000_000:
        return value * 1_000_000
    if 100_000_000_000_000 <= value < 100_000_000_000_000_000:
        return value * 1_000
    if 100_000_000_000_000_000 <= value < 100_000_000_000_000_000_000:
        return value
    if 1_000_000_000 <= value < 10_000_000_000:
        return value * NS
    raise LiveFeedProtocolError("unsupported Kalshi timestamp magnitude")


def _time_to_ns(value: object, field: str) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _epoch_to_ns(int(value))
        except (TypeError, ValueError) as exc:
            raise LiveFeedProtocolError(f"invalid Kalshi {field}") from exc
    if not isinstance(value, str) or not value.strip():
        raise LiveFeedProtocolError(f"invalid Kalshi {field}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LiveFeedProtocolError(f"invalid Kalshi {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * NS)


def _message_ts_ns(msg: dict, receive_ts_ns: int) -> int:
    if msg.get("ts_ms") is not None:
        return _epoch_to_ns(int(msg["ts_ms"]))
    if msg.get("ts") is not None:
        raw = msg["ts"]
        if isinstance(raw, str) and not raw.strip().isdigit():
            return _time_to_ns(raw, "message timestamp")
        return _epoch_to_ns(int(raw))
    return receive_ts_ns


async def _dispatch(callback: Callable[[object], object], event: object) -> None:
    result = callback(event)
    if inspect.isawaitable(result):
        await result


class KalshiLiveFeed:
    """Owns a separate authenticated market-data connection and in-memory book only."""

    def __init__(self, settings: CollectorSettings, *, series_ticker: str = "KXBTC15M") -> None:
        self.settings = settings
        self.series_ticker = series_ticker
        self._yes_levels: dict[str, dict[float, float]] = {}
        self._ask_levels: dict[str, dict[float, float]] = {}

    def subscription_messages(self, market_tickers: list[str]) -> list[dict]:
        messages: list[dict] = []
        next_id = 1
        tickers = sorted(set(market_tickers))
        if tickers:
            messages.append(
                {
                    "id": next_id,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": tickers,
                        "use_yes_price": True,
                    },
                }
            )
            messages.append(
                {
                    "id": next_id + 1,
                    "cmd": "subscribe",
                    "params": {"channels": ["trade"], "market_tickers": tickers},
                }
            )
            next_id += 2
        messages.extend(
            [
                {
                    "id": next_id,
                    "cmd": "subscribe",
                    "params": {"channels": ["cfbenchmarks_value"], "index_ids": ["BRTI"]},
                },
                {
                    "id": next_id + 1,
                    "cmd": "subscribe",
                    "params": {"channels": ["market_lifecycle_v2"]},
                },
            ]
        )
        return messages

    def market_from_events_payload(self, payload: dict) -> LiveMarket | None:
        candidates: list[LiveMarket] = []
        for event in payload.get("events", []):
            if not isinstance(event, dict) or event.get("series_ticker") != self.series_ticker:
                continue
            for raw in event.get("markets", []):
                if not isinstance(raw, dict) or raw.get("status") != "active" or not raw.get("ticker"):
                    continue
                strike_raw = raw.get("floor_strike")
                if strike_raw is None:
                    strike_raw = raw.get("floor_strike_dollars")
                if strike_raw is None:
                    strike_raw = raw.get("strike")
                if strike_raw is None:
                    continue
                try:
                    strike = _finite_float(strike_raw, "market strike", minimum=0.0)
                    if strike <= 0.0:
                        continue
                    open_ts_ns = _time_to_ns(raw.get("open_time") or raw.get("open_ts"), "market open time")
                    close_ts_ns = _time_to_ns(raw.get("close_time") or raw.get("close_ts"), "market close time")
                    if close_ts_ns <= open_ts_ns:
                        continue
                    oi_raw = raw.get("open_interest_fp", raw.get("open_interest", 0.0))
                    open_interest = _finite_float(oi_raw or 0.0, "open interest", minimum=0.0)
                except LiveFeedProtocolError:
                    continue
                candidates.append(
                    LiveMarket(
                        ticker=str(raw["ticker"]),
                        strike=strike,
                        open_ts_ns=open_ts_ns,
                        close_ts_ns=close_ts_ns,
                        open_interest=open_interest,
                    )
                )
        if not candidates:
            return None
        return min(candidates, key=lambda market: (market.close_ts_ns, market.ticker))

    def _quote(self, ticker: str, source_ts_ns: int) -> LiveQuote:
        yes = self._yes_levels.get(ticker, {})
        asks = self._ask_levels.get(ticker, {})
        return LiveQuote(
            market_ticker=ticker,
            source_ts_ns=source_ts_ns,
            yes_bid=max(yes) if yes else None,
            yes_ask=min(asks) if asks else None,
        )

    def process_message(self, payload: dict, *, receive_ts_ns: int) -> tuple[object, ...]:
        if not isinstance(payload, dict):
            raise LiveFeedProtocolError("Kalshi WebSocket payload must be an object")
        kind = str(payload.get("type", ""))
        msg = payload.get("msg")
        if not isinstance(msg, dict):
            if kind in {"subscribed", "ok"}:
                return ()
            raise LiveFeedProtocolError("Kalshi message payload is missing")

        if kind == "orderbook_snapshot":
            ticker = str(msg.get("market_ticker", ""))
            if not ticker:
                raise LiveFeedProtocolError("orderbook snapshot lacks ticker")
            try:
                yes = {float(price): float(quantity) for price, quantity in msg.get("yes_dollars_fp", []) if float(quantity) > 0.0}
                asks = {float(price): float(quantity) for price, quantity in msg.get("no_dollars_fp", []) if float(quantity) > 0.0}
            except (TypeError, ValueError) as exc:
                raise LiveFeedProtocolError("malformed orderbook snapshot") from exc
            if any(not math.isfinite(price) or price < 0.0 or price > 1.0 for price in (*yes, *asks)):
                raise LiveFeedProtocolError("invalid orderbook price")
            self._yes_levels[ticker] = yes
            self._ask_levels[ticker] = asks
            return (self._quote(ticker, _message_ts_ns(msg, receive_ts_ns)),)

        if kind == "orderbook_delta":
            ticker = str(msg.get("market_ticker", ""))
            side = str(msg.get("side", ""))
            if not ticker or side not in {"yes", "no"}:
                raise LiveFeedProtocolError("malformed orderbook delta")
            price = _finite_float(msg.get("price_dollars"), "orderbook price")
            delta = _finite_float(msg.get("delta_fp"), "orderbook delta")
            if not 0.0 <= price <= 1.0:
                raise LiveFeedProtocolError("invalid orderbook price")
            ladder = self._yes_levels.setdefault(ticker, {}) if side == "yes" else self._ask_levels.setdefault(ticker, {})
            new_quantity = ladder.get(price, 0.0) + delta
            if new_quantity < -1e-12:
                raise LiveFeedProtocolError("orderbook delta would create negative quantity")
            if new_quantity <= 1e-12:
                ladder.pop(price, None)
            else:
                ladder[price] = new_quantity
            return (self._quote(ticker, _message_ts_ns(msg, receive_ts_ns)),)

        if kind == "trade":
            ticker = str(msg.get("market_ticker", ""))
            if not ticker:
                raise LiveFeedProtocolError("trade lacks market ticker")
            price = _finite_float(msg.get("yes_price_dollars"), "trade yes price")
            count = _finite_float(msg.get("count_fp"), "trade count", minimum=0.0)
            side = str(msg.get("taker_outcome_side") or msg.get("taker_side") or "")
            if not 0.0 <= price <= 1.0 or side not in {"yes", "no"}:
                raise LiveFeedProtocolError("malformed trade direction/price")
            return (
                LiveTrade(
                    market_ticker=ticker,
                    source_ts_ns=_message_ts_ns(msg, receive_ts_ns),
                    yes_price=price,
                    count=count,
                    taker_side=side,
                ),
            )

        if kind == "cfbenchmarks_value":
            if str(msg.get("index_id", "")) != "BRTI":
                return ()
            raw_data = msg.get("data")
            if isinstance(raw_data, str):
                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError as exc:
                    raise LiveFeedProtocolError("malformed BRTI payload") from exc
            elif isinstance(raw_data, dict):
                data = raw_data
            else:
                raise LiveFeedProtocolError("BRTI data payload is missing")
            if not isinstance(data, dict):
                raise LiveFeedProtocolError("BRTI data payload is not an object")
            value_raw = next((data[key] for key in ("value", "price", "rate", "index_value") if data.get(key) is not None), None)
            time_raw = data.get("time") or data.get("ts_ms") or data.get("timestamp")
            if value_raw is None or time_raw is None:
                raise LiveFeedProtocolError("BRTI value/timestamp is missing")
            value = _finite_float(value_raw, "BRTI value", minimum=0.0)
            if value <= 0.0:
                raise LiveFeedProtocolError("BRTI value must be positive")
            return (BRTIObservation(source_ts_ns=_epoch_to_ns(int(time_raw)), value=value),)

        if kind == "market_lifecycle_v2":
            ticker = str(msg.get("market_ticker", ""))
            event_type = str(msg.get("event_type", ""))
            if not ticker or not event_type:
                raise LiveFeedProtocolError("malformed market lifecycle event")
            floor = msg.get("floor_strike")
            floor_strike = None if floor is None else _finite_float(floor, "lifecycle strike", minimum=0.0)
            if floor_strike is not None and floor_strike <= 0.0:
                raise LiveFeedProtocolError("lifecycle strike must be positive")
            return (
                MarketLifecycle(
                    market_ticker=ticker,
                    event_type=event_type,
                    receive_ts_ns=receive_ts_ns,
                    floor_strike=floor_strike,
                ),
            )

        if kind in {"subscribed", "ok", "unsubscribed"}:
            return ()
        if kind == "error":
            raise LiveFeedProtocolError(f"Kalshi subscription error: {msg.get('msg') or msg.get('code')}")
        return ()

    async def run_forever(self, callback: Callable[[object], object]) -> None:
        delay = self.settings.reconnect_initial_seconds
        while True:
            try:
                events_payload = await fetch_open_events_payload(
                    base_url=self.settings.rest_base_url,
                    series_ticker=self.series_ticker,
                )
                market = self.market_from_events_payload(events_payload)
                tickers = [] if market is None else [market.ticker]
                if market is not None:
                    await _dispatch(callback, market)

                key_id, private_key_path = self.settings.require_credentials()
                private_key = load_private_key(private_key_path)
                headers = create_auth_headers(
                    key_id=key_id,
                    private_key=private_key,
                    method="GET",
                    path="/trade-api/ws/v2",
                )
                async with websockets.connect(
                    self.settings.ws_url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=4096,
                ) as ws:
                    for message in self.subscription_messages(tickers):
                        await ws.send(json.dumps(message, separators=(",", ":")))
                    delay = self.settings.reconnect_initial_seconds
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.settings.stale_after_seconds)
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, TypeError) as exc:
                            raise LiveFeedProtocolError("malformed Kalshi WebSocket frame") from exc
                        for event in self.process_message(payload, receive_ts_ns=__import__("time").time_ns()):
                            await _dispatch(callback, event)
                            if isinstance(event, MarketLifecycle) and event.market_ticker.startswith(f"{self.series_ticker}-"):
                                if event.event_type in {
                                    "created",
                                    "activated",
                                    "deactivated",
                                    "close_date_updated",
                                    "determined",
                                    "settled",
                                    "metadata_updated",
                                }:
                                    raise LiveFeedProtocolError("target lifecycle changed; refreshing live market")
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.settings.reconnect_max_seconds)
