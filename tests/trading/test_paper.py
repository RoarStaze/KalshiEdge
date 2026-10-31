from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.trading.paper import PaperLedger, PaperTrader, PublicMarketClient
from kalshi_edge.trading.risk import PaperPlan

NS = 1_000_000_000
SCHEDULED = 1_800_000_000_000_000_000
TICKER = "KXBTC15M-TEST"


def plan():
    return PaperPlan(
        ticker=TICKER, outcome="ABOVE", price="0.5000", count="0.10",
        fee_reserve="0.0200", worst_case_debit="0.0700",
        estimated_net_profit="0.027000", model_win_probability="0.970000"
    )


def test_one_attempt_per_market_even_across_restarts(tmp_path):
    path = tmp_path / "paper.sqlite3"
    db = PaperLedger(path)
    assert db.reserve(TICKER, "ABOVE", SCHEDULED)
    db.finish(TICKER, status="PAPER_OPEN", plan=plan())
    assert not PaperLedger(path).reserve(TICKER, "ABOVE", SCHEDULED + NS)


def test_paper_settlement_and_daily_risk_budget(tmp_path):
    db = PaperLedger(tmp_path / "paper.sqlite3")
    assert db.reserve(TICKER, "ABOVE", SCHEDULED)
    db.finish(TICKER, status="PAPER_OPEN", plan=plan())
    state = db.risk_state(SCHEDULED + NS)
    assert Decimal(state["open_worst_case"]) == Decimal("0.07")
    db.settle(TICKER, "BELOW", SCHEDULED + 240 * NS)
    row = db.snapshot()[0]
    assert row["status"] == "SETTLED"
    assert Decimal(row["net_profit"]) == Decimal("-0.07")
    assert db.risk_state(SCHEDULED + 300 * NS)["consecutive_losses"] == 1
    db.settle(TICKER, "ABOVE", SCHEDULED + 240 * NS)
    assert db.snapshot()[0]["actual_side"] == "BELOW"


def test_http_transport_only_performs_public_gets():
    requested = []
    def handler(request):
        requested.append((request.method, request.url.path))
        return httpx.Response(200, json={"market": {"ticker": TICKER}})
    client = PublicMarketClient(transport=httpx.MockTransport(handler))
    try:
        market, book = client.quote(TICKER)
        assert requested == [
            ("GET", f"/trade-api/v2/markets/{TICKER}"),
            ("GET", f"/trade-api/v2/markets/{TICKER}/orderbook"),
        ]
    finally:
        client.close()


def test_paper_trading_starts_disabled(tmp_path):
    class DummyClient:
        def quote(self, ticker):
            raise AssertionError("disabled paper trading may not fetch quotes")
    trader = PaperTrader(tmp_path, client=DummyClient())
    assert trader.enabled() is False
    assert trader.status()["live_order_submission"] is False
    assert trader.status()["fractional_live_eligibility_verified"] is False
    trader.poll_once(SCHEDULED + NS)


def test_risk_history_stays_in_local_database(tmp_path):
    db = PaperLedger(tmp_path / "paper.sqlite3")
    assert db.snapshot() == []
    db.reserve("KXBTC15M-ONE", "SKIP", SCHEDULED)
    db.finish("KXBTC15M-ONE", status="SKIP", reason="confidence too low")
    assert db.snapshot()[0]["reason"] == "confidence too low"
