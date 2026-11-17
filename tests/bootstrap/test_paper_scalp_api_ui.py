"""Paper scalper controls are isolated from live AutoTrade and real accounts."""
from http.server import ThreadingHTTPServer
import json
import re
import shutil
import subprocess
import threading

import httpx

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.scalp_paper_ui import integrate_paper_scalp


class FakeScalper:
    def __init__(self):
        self.enabled=False
        self.calls=[]
    def status(self):
        return {
            "mode":"PAPER_SCALP_SIMULATED",
            "enabled":self.enabled,
            "live_order_submission":False,
            "recent_cycles":[],
            "open_position":None,
        }
    def arm(self, amount):
        self.calls.append(("arm",amount))
        self.enabled=True
        return self.status()
    def pause(self):
        self.calls.append(("pause",))
        self.enabled=False
        return self.status()


class Vault:
    def exists(self):
        return False


def http_server(root, scalper):
    handler=_make_handler(
        root, credential_vault=Vault(), paper_scalper=scalper,
    )
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server,thread


def test_paper_scalp_status_and_controls_never_require_live_credentials(tmp_path):
    fake=FakeScalper()
    server,thread=http_server(tmp_path,fake)
    base=f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(timeout=5,trust_env=False) as client:
            result=client.get(base+"/api/paper-scalp/status")
            assert result.status_code==200
            assert result.json()["live_order_submission"] is False
            assert client.get(base+"/api/armed-trade/status").status_code==503
            no_origin=client.post(base+"/api/paper-scalp/arm",json={"amount":"1"})
            assert no_origin.status_code==403
            malicious=client.post(base+"/api/paper-scalp/arm",json={"amount":"1"},
                headers={"Origin":"https://evil.example",
                         "X-KalshiEdge-Action":"paper-scalp",
                         "Sec-Fetch-Site":"cross-site"})
            assert malicious.status_code==403
            assert fake.calls==[]
            valid={"Origin":base,"X-KalshiEdge-Action":"paper-scalp",
                   "Sec-Fetch-Site":"same-origin"}
            assert client.post(base+"/api/paper-scalp/arm",
                               json={"amount":"1.00"},headers=valid).json()["enabled"]
            assert client.post(base+"/api/paper-scalp/arm",
                               json={"amount":"1.00","mode":"real"},headers=valid).status_code==409
            assert client.post(base+"/api/paper-scalp/pause",
                               json={},headers=valid).json()["enabled"] is False
            assert fake.calls==[("arm","1.00"),("pause",)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_paper_controls_render_as_separately_labeled_auto_panel():
    page=integrate_paper_scalp(integrate_manual_trade(PAGE))
    assert 'id="paper-scalp-panel"' in page
    assert 'id="paper-scalp-toggle"' in page
    assert 'id="paper-scalp-amount"' in page
    assert 'id="paper-scalp-results"' in page
    assert "PAPER ONLY" in page
    assert '"/api/paper-scalp/"+path' in page
    assert 'request("status")' in page
    assert 'request(armed?"pause":"arm"' in page
    assert '"X-KalshiEdge-Action":"paper-scalp"' in page
    assert 'id="manual-auto-arm"' in page # real AUTO preserved
    assert "simulated" in page.lower()


def test_paper_controls_javascript_syntax_is_valid():
    page=integrate_paper_scalp(integrate_manual_trade(PAGE))
    scripts=re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>",page,re.I)
    assert len(scripts)>=3
    if shutil.which("node"):
        for script in scripts:
            result=subprocess.run(["node","--check"],input=script,text=True,
                capture_output=True,timeout=10,check=False)
            assert result.returncode==0,result.stderr


def test_real_paper_scalper_can_arm_via_browser_api_without_kalshi_credentials(tmp_path):
    """Avoid testing solely fake handler objects for the actual Start workflow."""
    from kalshi_edge.trading.paper_scalper import PaperScalpTrader

    class PublicOnly:
        def quote(self, ticker):
            raise AssertionError("Arm is not allowed to request live order execution")
        def close(self):
            pass

    scalper=PaperScalpTrader(
        tmp_path, lambda: {"status": "WAITING"}, client=PublicOnly(),
        now_ns=lambda: 1_800_000_000_000_000_000,
    )
    server,thread=http_server(tmp_path,scalper)
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        headers={
            "Origin":base, "X-KalshiEdge-Action":"paper-scalp",
            "Sec-Fetch-Site":"same-origin",
        }
        with httpx.Client(timeout=5,trust_env=False) as client:
            status=client.get(base+"/api/paper-scalp/status")
            assert status.status_code==200
            assert status.json()["mode"]=="PAPER_SCALP_SIMULATED"
            assert not status.json()["enabled"]
            armed=client.post(base+"/api/paper-scalp/arm",
                              json={"amount":"1.00"},headers=headers)
            assert armed.status_code==200,armed.text
            assert armed.json()["enabled"] is True
            assert armed.json()["live_order_submission"] is False
            paused=client.post(base+"/api/paper-scalp/pause",
                               json={},headers=headers)
            assert paused.status_code==200
            assert paused.json()["enabled"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        scalper.close()
