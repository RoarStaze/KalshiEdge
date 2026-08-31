from __future__ import annotations

from typing import Any

import httpx


PRODUCTION_REST_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_REST_BASE = "https://demo-api.kalshi.co/trade-api/v2"


def extract_market_tickers(payload: dict[str, Any], series_ticker: str) -> list[str]:
    tickers: list[str] = []
    for event in payload.get("events", []):
        if event.get("series_ticker") != series_ticker:
            continue
        for market in event.get("markets", []):
            if market.get("status") == "open" and market.get("ticker"):
                tickers.append(str(market["ticker"]))
    return sorted(set(tickers))


async def fetch_open_events_payload(*, base_url: str, series_ticker: str) -> dict[str, Any]:
    params = {"series_ticker": series_ticker, "status": "open", "with_nested_markets": "true", "limit": 200}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/events", params=params)
        response.raise_for_status()
        return response.json()


async def fetch_series_payload(*, base_url: str, series_ticker: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/series/{series_ticker}")
        response.raise_for_status()
        return response.json()


async def fetch_market_payload(*, base_url: str, market_ticker: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/markets/{market_ticker}")
        response.raise_for_status()
        return response.json()


async def discover_open_markets(*, base_url: str, series_ticker: str) -> list[str]:
    payload = await fetch_open_events_payload(base_url=base_url, series_ticker=series_ticker)
    return extract_market_tickers(payload, series_ticker)
