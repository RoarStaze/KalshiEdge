from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.kalshi_history import KalshiHistoricalClient
from kalshi_edge.config import CollectorSettings


def _settings(tmp_path: Path) -> CollectorSettings:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)
    return CollectorSettings(key_id="test-key", private_key_path=key_path, env="production")


def test_discover_markets_pages_historical_and_current_without_duplicates(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        cursor = request.url.params.get("cursor")
        if path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={
                "market_settled_ts": "2026-08-01T00:00:00Z",
                "trades_created_ts": "2026-08-01T00:00:00Z",
                "orders_updated_ts": "2026-08-01T00:00:00Z",
                "market_positions_last_updated_ts": "2026-08-01T00:00:00Z",
            })
        if path.endswith("/historical/markets"):
            assert request.url.params["series_ticker"] == "KXBTC15M"
            assert request.url.params["limit"] == "1000"
            if cursor is None:
                return httpx.Response(200, json={"markets": [{"ticker": "OLD-1", "settlement_ts": "2026-01-01T00:00:00Z"}], "cursor": "next"})
            assert cursor == "next"
            return httpx.Response(200, json={"markets": [{"ticker": "DUP", "settlement_ts": "2026-02-01T00:00:00Z"}], "cursor": ""})
        if path.endswith("/markets"):
            assert request.url.params["series_ticker"] == "KXBTC15M"
            assert request.url.params["status"] == "settled"
            return httpx.Response(200, json={"markets": [
                {"ticker": "DUP", "settlement_ts": "2026-08-02T00:00:00Z"},
                {"ticker": "NEW-1", "settlement_ts": "2026-08-03T00:00:00Z"},
            ], "cursor": ""})
        raise AssertionError(f"unexpected request {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = KalshiHistoricalClient(_settings(tmp_path), BootstrapSettings(), http=http)
        markets = client.discover_markets()

    assert [market["ticker"] for market in markets] == ["DUP", "NEW-1", "OLD-1"]
    assert any("KALSHI-ACCESS-SIGNATURE" in request.headers for request in requests)


def test_fetch_trades_splits_at_cutoff_and_deduplicates(tmp_path: Path) -> None:
    cutoff_epoch = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={
                "market_settled_ts": "2026-08-01T00:00:00Z",
                "trades_created_ts": "2026-08-01T00:00:00Z",
                "orders_updated_ts": "2026-08-01T00:00:00Z",
                "market_positions_last_updated_ts": "2026-08-01T00:00:00Z",
            })
        if path.endswith("/historical/trades"):
            assert request.url.params["ticker"] == "KXBTC15M-X"
            assert int(request.url.params["max_ts"]) == cutoff_epoch
            return httpx.Response(200, json={"trades": [{"trade_id": "old"}, {"trade_id": "dup"}], "cursor": ""})
        if path.endswith("/markets/trades"):
            assert request.url.params["ticker"] == "KXBTC15M-X"
            assert int(request.url.params["min_ts"]) == cutoff_epoch
            return httpx.Response(200, json={"trades": [{"trade_id": "dup"}, {"trade_id": "new"}], "cursor": ""})
        raise AssertionError(path)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = KalshiHistoricalClient(_settings(tmp_path), BootstrapSettings(), http=http)
        trades = client.fetch_trades("KXBTC15M-X")

    assert [trade["trade_id"] for trade in trades] == ["old", "dup", "new"]


def test_fetch_candlesticks_routes_by_discovered_archive_status(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        path = request.url.path
        if path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={
                "market_settled_ts": "2026-08-01T00:00:00Z",
                "trades_created_ts": "2026-08-01T00:00:00Z",
                "orders_updated_ts": "2026-08-01T00:00:00Z",
                "market_positions_last_updated_ts": "2026-08-01T00:00:00Z",
            })
        if path.endswith("/historical/markets"):
            return httpx.Response(200, json={"markets": [{"ticker": "OLD", "settlement_ts": "2026-01-01T00:00:00Z"}], "cursor": ""})
        if path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [{"ticker": "NEW", "settlement_ts": "2026-08-02T00:00:00Z"}], "cursor": ""})
        if path.endswith("/historical/markets/OLD/candlesticks"):
            assert request.url.params["period_interval"] == "1"
            return httpx.Response(200, json={"ticker": "OLD", "candlesticks": [{"end_period_ts": 1}]})
        if path.endswith("/series/KXBTC15M/markets/NEW/candlesticks"):
            return httpx.Response(200, json={"ticker": "NEW", "candlesticks": [{"end_period_ts": 2}]})
        raise AssertionError(path)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = KalshiHistoricalClient(_settings(tmp_path), BootstrapSettings(), http=http)
        client.discover_markets()
        assert client.fetch_candlesticks("OLD", start_ts=1, end_ts=2) == [{"end_period_ts": 1}]
        assert client.fetch_candlesticks("NEW", start_ts=1, end_ts=2) == [{"end_period_ts": 2}]

    assert any(path.endswith("/historical/markets/OLD/candlesticks") for path in seen)
    assert any(path.endswith("/series/KXBTC15M/markets/NEW/candlesticks") for path in seen)


def test_rate_limit_retries_using_retry_after(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"}, json={"message": "slow down"})
        return httpx.Response(200, json={
            "market_settled_ts": "2026-08-01T00:00:00Z",
            "trades_created_ts": "2026-08-01T00:00:00Z",
            "orders_updated_ts": "2026-08-01T00:00:00Z",
            "market_positions_last_updated_ts": "2026-08-01T00:00:00Z",
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = KalshiHistoricalClient(_settings(tmp_path), BootstrapSettings(), http=http, sleep=sleeps.append)
        client.get_cutoff()

    assert calls == 2
    assert sleeps == [0.25]


def test_backfill_uses_website_ed25519_key_instead_of_revoked_legacy_key(tmp_path: Path) -> None:
    """Historic read-only REST requests use the same DPAPI key as live WS."""
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ).decode()
    class StoredVault:
        def exists(self):
            return True
        def load(self):
            return ("fresh-website-key", pem)
    def revoked():
        raise AssertionError("must not load revoked old KALSHI_API_KEY_ID")
    settings = SimpleNamespace(
        require_credentials=revoked,
        rest_base_url="https://example.test/trade-api/v2"
    )
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "market_settled_ts": "2026-08-01T00:00:00Z",
            "trades_created_ts": "2026-08-01T00:00:00Z",
            "orders_updated_ts": "2026-08-01T00:00:00Z",
            "market_positions_last_updated_ts": "2026-08-01T00:00:00Z",
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = KalshiHistoricalClient(
            settings, BootstrapSettings(), http=http, vault=StoredVault()
        )
        client.get_cutoff()
    assert len(seen) == 1
    assert seen[0].headers["KALSHI-ACCESS-KEY"] == "fresh-website-key"
    import base64
    sig = base64.b64decode(seen[0].headers["KALSHI-ACCESS-SIGNATURE"])
    ts = seen[0].headers["KALSHI-ACCESS-TIMESTAMP"]
    private.public_key().verify(
        sig, (ts + "GET/trade-api/v2/historical/cutoff").encode()
    )


def test_historical_client_falls_back_only_when_no_saved_website_key(tmp_path: Path) -> None:
    class EmptyVault:
        def exists(self):
            return False
    settings = _settings(tmp_path)
    client = KalshiHistoricalClient(settings, BootstrapSettings(), vault=EmptyVault())
    try:
        assert client.key_id == "test-key"
        assert isinstance(client.private_key, rsa.RSAPrivateKey)
    finally:
        client.close()


def test_historical_client_corrupt_saved_vault_fails_closed(tmp_path: Path) -> None:
    from types import SimpleNamespace
    import pytest
    from kalshi_edge.trading.vault import VaultError
    class BrokenVault:
        def exists(self):
            return True
        def load(self):
            raise VaultError("saved DPAPI key cannot be decrypted")
    settings = SimpleNamespace(require_credentials=lambda: (_ for _ in ()).throw(
        AssertionError("unsafe revoked-key fallback")
    ))
    with pytest.raises(VaultError, match="cannot be decrypted"):
        KalshiHistoricalClient(settings, BootstrapSettings(), vault=BrokenVault())
