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


def test_real_order_path_is_only_one_mocked_exchange_submission(tmp_path):
    from datetime import timedelta
    import uuid

    class Vault:
        def exists(self): return True
    class Public:
        def quote(self,ticker):
            m={"market":dict(MARKET["market"])}
            m["market"]["close_time"]=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()
            return m,BOOK
        def close(self): pass
    class API:
        calls=0
        def balance(self): return Decimal("3.14")
        def position(self,ticker): return Decimal("-0.16")
        def create(self,ticker,plan,request_id):
            self.calls+=1
            assert plan["reduce_only"] is True
            assert plan["book_side"]=="bid"
            assert plan["v2_yes_price"]=="0.7300"
            return {"order_id":"a32c7257-b054-40aa-ac53-8f1c57ceab40",
                    "fill_count":"0.16","remaining_count":"0.00"}
        def fills(self,order_id):
            return {"fills":[{"order_id":order_id,"count_fp":"0.16",
                "yes_price_dollars":"0.7300","no_price_dollars":"0.2700",
                "fee_cost":"0.0000"}]}
        def close(self): pass

    api=API()
    trader=ManualTrading(tmp_path,Vault(),api=api,public=Public(),
                         live_reader=lambda _:{"market_ticker":TICKER})
    request={"ticker":TICKER,"action":"sell","side":"no","mode":"shares",
             "amount":"0.16","quoted_price":"0.2700","request_id":str(uuid.uuid4())}
    try:
        filled=trader.submit(request)
        assert filled["state"]=="FILLED"
        assert filled["actual_total_dollars"]=="0.043200"
        assert filled["actual_fees_dollars"]=="0.0000"
        assert api.calls==1
        with pytest.raises(ManualTradeError,match="already processed"):
            trader.submit(request)
        assert api.calls==1
    finally:
        trader.close()


def test_ed25519_signatures_use_v2_path_without_query_signature():
    import json
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    from kalshi_edge.trading.manual import KalshiManualClient
    key=ed25519.Ed25519PrivateKey.generate()
    pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    received=[]
    class Vault:
        def load(self): return "test-key",pem
    def handler(req):
        message=(req.headers["KALSHI-ACCESS-TIMESTAMP"]+req.method+req.url.path).encode()
        import base64
        key.public_key().verify(
            base64.b64decode(req.headers["KALSHI-ACCESS-SIGNATURE"]),message
        )
        received.append((req.method,req.url.path,req.url.query))
        if req.method=="GET":
            return httpx.Response(200,json={"market_positions":[{"ticker":TICKER,"position_fp":"-0.16"}]})
        payload=json.loads(req.content)
        assert payload["side"]=="ask"
        assert payload["reduce_only"] is False
        assert payload["time_in_force"]=="immediate_or_cancel"
        return httpx.Response(201,json={"order_id":"ab","fill_count":"0.00"})
    client=KalshiManualClient(Vault(),transport=httpx.MockTransport(handler))
    try:
        assert client.position(TICKER)==Decimal("-0.16")
        client.create(TICKER,{"book_side":"ask","count":"0.16",
            "v2_yes_price":"0.3000","reduce_only":False}, 
            "8c35ecb3-328f-4f52-8c7c-0f4b9862f8d1")
        assert [row[0] for row in received]==["GET","POST"]
        assert received[0][1]=="/trade-api/v2/portfolio/positions"
        assert received[1][1]=="/trade-api/v2/portfolio/events/orders"
    finally:
        client.close()


def test_rsa_pss_production_api_key_authentication():
    import base64
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import serialization, hashes
    from kalshi_edge.trading.manual import KalshiManualClient
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pem=key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    class Vault:
        def load(self): return "rsa-test",pem
    def handler(req):
        sig=base64.b64decode(req.headers["KALSHI-ACCESS-SIGNATURE"])
        signed=(req.headers["KALSHI-ACCESS-TIMESTAMP"]+req.method+req.url.path).encode()
        key.public_key().verify(sig,signed,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=hashes.SHA256().digest_size),hashes.SHA256())
        return httpx.Response(200,json={"balance_dollars":"3.1400"})
    client=KalshiManualClient(Vault(),transport=httpx.MockTransport(handler))
    try:
        assert client.balance()==Decimal("3.1400")
    finally:
        client.close()
