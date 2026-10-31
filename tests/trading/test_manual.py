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
        plan_manual_order(q, "buy", "yes", "dollars", "0.01", Decimal("0.20"),
                          Decimal("0"), fractional=True)

def test_html_only_replaces_two_context_cards():
    page = integrate_manual_trade_card(PAGE)
    assert "id=\"manual-trader\"" in page
    assert "Buy with 1-Click" in page
    assert '"Sell"' in page and '" with 1-Click"' in page
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


def test_same_origin_manual_order_requires_direct_browser_action(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from kalshi_edge.bootstrap.final_history import _make_handler

    class Vault:
        def exists(self): return True
        def save(self, a, b): pass
        def delete(self): pass
    class Manual:
        calls = 0
        def status(self): return {"available":False,"credentials_saved":True}
        def submit(self, request):
            self.calls += 1
            return {"state":"FILLED","order_id":"confirmed-in-test"}
    manual = Manual()
    server=ThreadingHTTPServer(("127.0.0.1",0),_make_handler(
        tmp_path,credential_vault=Vault(),manual_service=manual))
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        origin=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=4,trust_env=False) as http:
            page=http.get(origin+"/")
            assert page.status_code==200
            assert 'id="manual-trader"' in page.text
            assert ".context.manual-trade-context>.context-header" in page.text
            assert http.get(origin+"/api/manual/status").json()["credentials_saved"] is True
            def post(host):
                return http.post(origin+"/api/manual/order",json={"amount":"1.00"},
                    headers={"Origin":host,"X-KalshiEdge-Action":"manual",
                             "Sec-Fetch-Site":"same-origin"})
            assert post("http://malicious.example").status_code==403
            assert manual.calls==0
            accepted=post(origin)
            assert accepted.status_code==200
            assert manual.calls==1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
