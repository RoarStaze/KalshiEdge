"""Manual Kalshi trade card: deterministic calculations and mocked exchange tests."""
from decimal import Decimal
from types import SimpleNamespace
from datetime import datetime, timezone
import httpx
import pytest

from kalshi_edge.trading.manual import (
    ManualTrading, ManualTradeError, quote_from_orderbook, plan_manual_order,
)
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade_card
from kalshi_edge.bootstrap.dashboard_ui import PAGE

TICKER = "KXBTC15M-26OCT301500-00"
BOOK = {"orderbook_fp": {
    "yes_dollars": [["0.3000", "9.90"]],
    "no_dollars": [["0.2700", "3.50"]],
}}
MARKET = {"market": {"ticker": TICKER, "status": "active", "fractional_trading_enabled": True,
                     "close_time": "2026-10-30T20:15:00Z"}}

def test_quote_prices_and_v2_book_sides():
    q = quote_from_orderbook(BOOK)
    assert q["yes"]["buy"]["price"] == "0.7300"
    assert q["yes"]["sell"]["price"] == "0.3000"
    assert q["no"]["buy"]["price"] == "0.7000"
    assert q["no"]["sell"]["price"] == "0.2700"
    assert q["yes"]["buy"]["book_side"] == "bid"
    assert q["no"]["buy"]["book_side"] == "ask"
    assert q["yes"]["sell"]["book_side"] == "ask"
    assert q["no"]["sell"]["book_side"] == "bid"

def test_budget_and_positions_are_never_exceeded():
    q = quote_from_orderbook(BOOK)
    p = plan_manual_order(q, "buy", "yes", "dollars", "1.00",
                          Decimal("3.14"), Decimal("0"), fractional=True)
    assert p["book_side"] == "bid"
    assert Decimal(p["count"]) % Decimal("0.01") == 0
    assert Decimal(p["estimated_total"]) <= Decimal("1.00")
    p = plan_manual_order(q, "sell", "no", "shares", "0.16",
                          Decimal("0"), Decimal("-0.16"), fractional=True)
    assert p["book_side"] == "bid"
    assert p["reduce_only"] is True
    assert Decimal(p["count"]) == Decimal("0.16")
    with pytest.raises(ManualTradeError):
        plan_manual_order(q, "sell", "yes", "shares", "1", Decimal("0"),
                          Decimal("-1"), fractional=True)
    with pytest.raises(ManualTradeError):
        plan_manual_order(q, "buy", "yes", "dollars", "1", Decimal("0.20"),
                          Decimal("0"), fractional=True)

def test_html_only_replaces_two_context_cards():
    page = integrate_manual_trade_card(PAGE)
    assert "id=\"manual-trader\"" in page
    assert "Buy with 1-Click" in page
    assert "Sell with 1-Click" in page
    assert ".context.manual-trade-context>.context-header" in page
    assert 'id="market-ticker"' in page
    assert 'id="feed-status"' in page
    assert '<article class="card forecast">' in page
    assert '<section class="card training-card"' in page

def test_manual_trade_does_not_submit_without_user_request(tmp_path):
    class Vault:
        def exists(self): return False
    service = ManualTrading(tmp_path, Vault())
    with pytest.raises(ManualTradeError):
        service.submit({"ticker": TICKER, "action": "buy", "side": "yes",
                        "mode": "dollars", "amount": "1", "request_id": "8c35ecb3-328f-4f52-8c7c-0f4b9862f8d1",
                        "quoted_price": "0.7300"})
    service.close()
