from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from kalshi_edge.bootstrap.backfill import backfill_kalshi
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.provenance import verify_artifact
from kalshi_edge.config import CollectorSettings


MARKET = {
    "ticker": "KXBTC15M-TEST",
    "event_ticker": "KXBTC15M-EVENT",
    "market_type": "binary",
    "open_time": "2026-08-01T12:00:00Z",
    "close_time": "2026-08-01T12:15:00Z",
    "settlement_ts": "2026-08-01T12:16:00Z",
    "status": "finalized",
    "result": "yes",
    "settlement_value_dollars": "100100.2500",
    "strike_type": "greater",
    "floor_strike": 100000.0,
    "rules_primary": "Resolves Yes if above target.",
    "rules_secondary": "CF Benchmarks",
    "is_provisional": False,
}


class FakeClient:
    def __init__(self) -> None:
        self.market_fetches = 0
        self.trade_fetches = 0
        self.candle_fetches = 0

    def discover_markets(self):
        return [MARKET]

    def fetch_market(self, ticker: str):
        self.market_fetches += 1
        return {"market": MARKET}

    def fetch_trades(self, ticker: str):
        self.trade_fetches += 1
        return [{"trade_id": "t1", "ticker": ticker, "created_time": "2026-08-01T12:01:00Z"}]

    def fetch_candlesticks(self, ticker: str, *, start_ts: int, end_ts: int, period_interval: int = 1):
        self.candle_fetches += 1
        assert period_interval == 1
        assert end_ts > start_ts
        return [{"end_period_ts": start_ts + 60, "volume": 1}]

    def get_cutoff(self):
        class Cutoff:
            def model_dump(self, **kwargs):
                return {"market_settled_ts": "2026-08-01T00:00:00Z"}
        return Cutoff()

    def close(self):
        pass


def test_backfill_kalshi_writes_verified_isolated_artifacts(tmp_path: Path) -> None:
    bootstrap = BootstrapSettings(bootstrap_dir=tmp_path / "data" / "bootstrap")
    client = FakeClient()
    report = backfill_kalshi(CollectorSettings(), bootstrap, client=client)

    assert report.market_count == 1
    assert report.downloaded_artifacts == 4
    assert report.excluded_markets == ()
    assert not (tmp_path / "data" / "raw").exists()

    for relative in (
        "raw/kalshi/cutoff.json",
        "raw/kalshi/markets/KXBTC15M-TEST.json",
        "raw/kalshi/trades/KXBTC15M-TEST.json",
        "raw/kalshi/candlesticks/KXBTC15M-TEST.json",
    ):
        path = bootstrap.bootstrap_dir / relative
        manifest = bootstrap.bootstrap_dir / "manifests" / "kalshi" / Path(relative).relative_to("raw/kalshi")
        manifest = Path(f"{manifest}.manifest.json")
        assert path.exists()
        assert verify_artifact(path, manifest)


def test_backfill_rerun_skips_verified_artifacts(tmp_path: Path) -> None:
    bootstrap = BootstrapSettings(bootstrap_dir=tmp_path / "data" / "bootstrap")
    first = FakeClient()
    backfill_kalshi(CollectorSettings(), bootstrap, client=first)

    second = FakeClient()
    report = backfill_kalshi(CollectorSettings(), bootstrap, client=second)

    assert report.downloaded_artifacts == 0
    assert report.skipped_artifacts == 4
    assert second.market_fetches == 0
    assert second.trade_fetches == 0
    assert second.candle_fetches == 0


def test_backfill_persists_but_excludes_ambiguous_market(tmp_path: Path) -> None:
    ambiguous = dict(MARKET)
    ambiguous["strike_type"] = "custom"

    class AmbiguousClient(FakeClient):
        def discover_markets(self):
            return [ambiguous]

        def fetch_market(self, ticker: str):
            return {"market": ambiguous}

    bootstrap = BootstrapSettings(bootstrap_dir=tmp_path / "data" / "bootstrap")
    report = backfill_kalshi(CollectorSettings(), bootstrap, client=AmbiguousClient())

    assert report.excluded_markets == ("KXBTC15M-TEST",)
    raw = bootstrap.bootstrap_dir / "raw/kalshi/markets/KXBTC15M-TEST.json"
    assert json.loads(raw.read_text(encoding="utf-8"))["market"]["strike_type"] == "custom"


def _http_error(status_code: int, ticker: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://example.test/historical/markets/{ticker}/candlesticks")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def test_backfill_excludes_only_market_whose_historical_candlesticks_are_404(tmp_path: Path) -> None:
    market_a = dict(MARKET)
    market_a["ticker"] = "KXBTC15M-A"
    market_b = dict(MARKET)
    market_b["ticker"] = "KXBTC15M-B"

    class MissingCandleClient(FakeClient):
        def discover_markets(self):
            return [market_a, market_b]

        def fetch_market(self, ticker: str):
            self.market_fetches += 1
            return {"market": market_a if ticker == market_a["ticker"] else market_b}

        def fetch_candlesticks(self, ticker: str, *, start_ts: int, end_ts: int, period_interval: int = 1):
            self.candle_fetches += 1
            if ticker == market_b["ticker"]:
                raise _http_error(404, ticker)
            return super().fetch_candlesticks(
                ticker,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period_interval,
            )

    bootstrap = BootstrapSettings(bootstrap_dir=tmp_path / "data" / "bootstrap")
    report = backfill_kalshi(CollectorSettings(), bootstrap, client=MissingCandleClient())

    assert report.market_count == 2
    assert report.excluded_markets == (market_b["ticker"],)
    assert (bootstrap.bootstrap_dir / f"raw/kalshi/candlesticks/{market_a['ticker']}.json").exists()
    assert (bootstrap.bootstrap_dir / f"raw/kalshi/trades/{market_b['ticker']}.json").exists()
    assert not (bootstrap.bootstrap_dir / f"raw/kalshi/candlesticks/{market_b['ticker']}.json").exists()
    assert not (
        bootstrap.bootstrap_dir
        / f"manifests/kalshi/candlesticks/{market_b['ticker']}.json.manifest.json"
    ).exists()


def test_backfill_does_not_suppress_non_404_candlestick_failure(tmp_path: Path) -> None:
    class ServerErrorClient(FakeClient):
        def fetch_candlesticks(self, ticker: str, *, start_ts: int, end_ts: int, period_interval: int = 1):
            raise _http_error(500, ticker)

    bootstrap = BootstrapSettings(bootstrap_dir=tmp_path / "data" / "bootstrap")
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        backfill_kalshi(CollectorSettings(), bootstrap, client=ServerErrorClient())
    assert exc_info.value.response.status_code == 500
