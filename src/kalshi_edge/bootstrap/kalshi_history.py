from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from time import sleep as default_sleep
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from ..auth import create_auth_headers, load_private_key
from ..config import CollectorSettings
from .config import BootstrapSettings


class HistoricalCutoff(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_settled_ts: datetime
    trades_created_ts: datetime
    orders_updated_ts: datetime
    market_positions_last_updated_ts: datetime

    @property
    def trades_created_epoch(self) -> int:
        return int(self.trades_created_ts.timestamp())


class KalshiHistoricalClient:
    """Read-only client spanning Kalshi's current and historical market-data stores."""

    def __init__(
        self,
        settings: CollectorSettings,
        bootstrap: BootstrapSettings,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = default_sleep,
        max_attempts: int = 4,
    ) -> None:
        key_id, private_key_path = settings.require_credentials()
        self.settings = settings
        self.bootstrap = bootstrap
        self.key_id = key_id
        self.private_key = load_private_key(private_key_path)
        self._http = http or httpx.Client(timeout=20.0)
        self._owns_http = http is None
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._cutoff: HistoricalCutoff | None = None
        self._route_by_ticker: dict[str, Literal["historical", "current"]] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "KalshiHistoricalClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _signature_path(self, path: str) -> str:
        base_path = urlparse(self.settings.rest_base_url).path.rstrip("/")
        return f"{base_path}/{path.lstrip('/')}"

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.settings.rest_base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = create_auth_headers(
            key_id=self.key_id,
            private_key=self.private_key,
            method="GET",
            path=self._signature_path(path),
        )
        for attempt in range(self._max_attempts):
            response = self._http.get(url, params=params, headers=headers)
            if response.status_code == 429 and attempt + 1 < self._max_attempts:
                raw_retry = response.headers.get("Retry-After")
                try:
                    delay = float(raw_retry) if raw_retry is not None else min(0.5 * (2**attempt), 8.0)
                except ValueError:
                    delay = min(0.5 * (2**attempt), 8.0)
                self._sleep(max(0.0, delay))
                continue
            if response.status_code >= 500 and attempt + 1 < self._max_attempts:
                self._sleep(min(0.5 * (2**attempt), 8.0))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError(f"expected JSON object from {path}")
            return payload
        raise RuntimeError("unreachable retry loop")

    def _paginate(self, path: str, *, item_key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cursor: str | None = None
        items: list[dict[str, Any]] = []
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            payload = self._get_json(path, params=page_params)
            page_items = payload.get(item_key, [])
            if not isinstance(page_items, list):
                raise TypeError(f"{item_key} must be a list")
            items.extend(item for item in page_items if isinstance(item, dict))
            cursor_value = payload.get("cursor")
            cursor = str(cursor_value) if cursor_value else None
            if cursor is None:
                return items

    def get_cutoff(self) -> HistoricalCutoff:
        if self._cutoff is None:
            payload = self._get_json("/historical/cutoff")
            self._cutoff = HistoricalCutoff.model_validate(payload)
        return self._cutoff

    def discover_markets(self) -> list[dict[str, Any]]:
        historical = self._paginate(
            "/historical/markets",
            item_key="markets",
            params={"series_ticker": self.bootstrap.series_ticker, "limit": 1000},
        )
        current = self._paginate(
            "/markets",
            item_key="markets",
            params={"series_ticker": self.bootstrap.series_ticker, "status": "settled", "limit": 1000},
        )

        by_ticker: dict[str, dict[str, Any]] = {}
        for route, markets in (("historical", historical), ("current", current)):
            for market in markets:
                ticker = market.get("ticker")
                if not ticker:
                    continue
                ticker_text = str(ticker)
                by_ticker[ticker_text] = market
                self._route_by_ticker[ticker_text] = route
        return [by_ticker[ticker] for ticker in sorted(by_ticker)]

    def fetch_market(self, ticker: str) -> dict[str, Any]:
        route = self._route_by_ticker.get(ticker)
        if route == "historical":
            return self._get_json(f"/historical/markets/{ticker}")
        if route == "current":
            return self._get_json(f"/markets/{ticker}")

        try:
            payload = self._get_json(f"/markets/{ticker}")
            self._route_by_ticker[ticker] = "current"
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        payload = self._get_json(f"/historical/markets/{ticker}")
        self._route_by_ticker[ticker] = "historical"
        return payload

    def fetch_trades(self, ticker: str) -> list[dict[str, Any]]:
        cutoff = self.get_cutoff().trades_created_epoch
        historical = self._paginate(
            "/historical/trades",
            item_key="trades",
            params={"ticker": ticker, "max_ts": cutoff, "limit": 1000},
        )
        current = self._paginate(
            "/markets/trades",
            item_key="trades",
            params={"ticker": ticker, "min_ts": cutoff, "limit": 1000},
        )
        output: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for trade in [*historical, *current]:
            trade_id = trade.get("trade_id")
            key = str(trade_id) if trade_id is not None else repr(sorted(trade.items()))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            output.append(trade)
        return output

    def fetch_candlesticks(
        self,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: int = 1,
    ) -> list[dict[str, Any]]:
        route = self._route_by_ticker.get(ticker)
        if route is None:
            self.fetch_market(ticker)
            route = self._route_by_ticker[ticker]
        if route == "historical":
            path = f"/historical/markets/{ticker}/candlesticks"
        else:
            path = f"/series/{self.bootstrap.series_ticker}/markets/{ticker}/candlesticks"
        payload = self._get_json(
            path,
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
        candles = payload.get("candlesticks", [])
        if not isinstance(candles, list):
            raise TypeError("candlesticks must be a list")
        return [candle for candle in candles if isinstance(candle, dict)]
