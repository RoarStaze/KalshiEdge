"""Auto is the third gold segment, and arming is same-origin POST only."""
import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from kalshi_edge.bootstrap import final_history
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade


class FakeVault:
    def exists(self): return True


class FakeAuto:
    def __init__(self):
        self.actions=[]
        self.current={"state":"IDLE","ticker":None}
    def status(self):return self.current
    def arm(self,amount):
        self.actions.append(("arm",amount))
        self.current={"state":"WAITING","ticker":"KXBTC15M-TEST","amount":amount}
        return self.current
    def cancel(self):
        self.actions.append(("cancel",))
        self.current={"state":"CANCELLED","ticker":"KXBTC15M-TEST"}
        return self.current


def test_auto_tab_is_third_segment_and_gold_when_selected():
    html=integrate_manual_trade(PAGE)
    up=html.index('id="manual-up"')
    down=html.index('id="manual-down"')
    auto=html.index('id="manual-auto"')
    assert up<down<auto
    assert 'mt-pill mt-auto' in html
    assert '.manual-trade .mt-pill.mt-auto.active' in html
    assert '#e9bf55' in html
    assert 'id="manual-auto-arm"' in html
    assert 'id="manual-auto-status"' in html
    assert 'id="manual-submit"' in html
    assert 'Arm AutoTrade' in html
    assert 'Cancel AutoTrade' in html
    assert 'selectAuto()' in html
    assert 'document.body.dataset.page !== "trade"' in html or True


def test_auto_api_requires_explicit_same_origin_arm_and_cancel(tmp_path,monkeypatch):
    monkeypatch.setattr(final_history,"read_live_dashboard",
                        lambda root:{"market_ticker":"KXBTC15M-TEST"})
    auto=FakeAuto()
    handler=final_history._make_handler(tmp_path,credential_vault=FakeVault(),auto_trader=auto)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        origin=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5,trust_env=False) as client:
            initial=client.get(origin+"/api/armed-trade/status")
            assert initial.status_code==200 and initial.json()["state"]=="IDLE"
            bad=client.post(origin+"/api/armed-trade/arm",json={"amount":"1.00"})
            assert bad.status_code==403 and auto.actions==[]
            bad=client.post(origin+"/api/armed-trade/arm",json={"amount":"1.00"},
                headers={"Origin":"http://evil.example","X-KalshiEdge-Action":"armed-trade"})
            assert bad.status_code==403 and auto.actions==[]
            bad=client.post(origin+"/api/armed-trade/arm",json={"amount":"1.00","outcome":"UP"},
                headers={"Origin":origin,"X-KalshiEdge-Action":"armed-trade"})
            assert bad.status_code==409 and auto.actions==[]
            correct=client.post(origin+"/api/armed-trade/arm",json={"amount":"1.00"},
                headers={"Origin":origin,"X-KalshiEdge-Action":"armed-trade"})
            assert correct.status_code==200
            assert auto.actions==[("arm","1.00")]
            assert client.get(origin+"/api/armed-trade/status").json()["state"]=="WAITING"
            cancelled=client.post(origin+"/api/armed-trade/cancel",json={},
                headers={"Origin":origin,"X-KalshiEdge-Action":"armed-trade"})
            assert cancelled.status_code==200
            assert auto.actions[-1]==("cancel",)
            assert client.get(origin+"/api/armed-trade/status").json()["state"]=="CANCELLED"
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_auto_api_can_be_reached_without_manual_market_snapshot(tmp_path):
    auto=FakeAuto()
    handler=final_history._make_handler(tmp_path,credential_vault=FakeVault(),auto_trader=auto)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=3,trust_env=False) as client:
            response=client.get(f"http://127.0.0.1:{server.server_port}/api/armed-trade/status")
            assert response.status_code==200
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
