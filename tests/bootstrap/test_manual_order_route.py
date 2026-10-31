"""Dashboard manual order action: one explicit, same-origin click per request."""
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.bootstrap import final_history


class FakeVault:
    def exists(self): return True

class FakeManual:
    def __init__(self): self.sent=[]
    def market(self,ticker):return {"ticker":ticker,"status":"active","up_ask":"0.7000","down_ask":"0.3000","available_balance":"3.14","position_yes":"0.16"}
    def preview(self,ticker,action,outcome,units,amount):
        return {"preview_id":"test-once","ticker":ticker,"action":action,"outcome":outcome,"units":units,"count":"0.10","amount":amount}
    def place(self,preview_id,ticker):
        self.sent.append((preview_id,ticker))
        return {"status":"FILLED","order_id":"exchange-test","fill_count":"0.10"}

def test_manual_routes_require_explicit_same_origin_order_action(tmp_path,monkeypatch):
    ticker="KXBTC15M-TEST"
    monkeypatch.setattr(final_history,"read_live_dashboard",lambda root: {"market_ticker":ticker})
    manual=FakeManual()
    app=final_history._make_handler(tmp_path,manual_trader=manual,credential_vault=FakeVault())
    server=ThreadingHTTPServer(("127.0.0.1",0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        origin=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=3,trust_env=False) as http:
            page=http.get(origin+"/")
            assert page.status_code==200
            assert 'id="manual-trade"' in page.text
            assert ".context>.context-header,.context>.health{display:none!important}" in page.text
            market=http.get(origin+"/api/manual-trade/market")
            assert market.status_code==200 and market.json()["available_balance"]=="3.14"
            preview=http.get(origin+"/api/manual-trade/preview",
                params={"action":"BUY","outcome":"DOWN","units":"DOLLARS","amount":"0.50"})
            assert preview.status_code==200
            assert preview.json()["preview_id"]=="test-once"
            bad=http.post(origin+"/api/manual-trade/order",json={"preview_id":"test-once"},
                          headers={"Origin":"http://evil.example","X-KalshiEdge-Action":"manual-trade","Sec-Fetch-Site":"same-origin"})
            assert bad.status_code==403 and manual.sent==[]
            good=http.post(origin+"/api/manual-trade/order",json={"preview_id":"test-once"},
                           headers={"Origin":origin,"X-KalshiEdge-Action":"manual-trade","Sec-Fetch-Site":"same-origin"})
            assert good.status_code==200 and len(manual.sent)==1
            assert manual.sent[0]==("test-once",ticker)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
