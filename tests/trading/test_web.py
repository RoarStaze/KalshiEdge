import http.client
from http.server import ThreadingHTTPServer
import json
import threading

from kalshi_edge.trading.web import make_handler


class DummyTrader:
    def __init__(self):
        self.running = False
    def enable(self, value):
        self.running = value
    def enabled(self):
        return self.running
    def status(self):
        return {"enabled": self.running, "risk": {}, "recent_attempts": []}


class DummyVault:
    def __init__(self):
        self.saved = None
    def exists(self):
        return self.saved is not None
    def save(self, key, pem):
        self.saved = (key, pem)
    def delete(self):
        self.saved = None


def test_local_web_api_blocks_cross_origin_actions_and_never_returns_pem():
    trader = DummyTrader()
    vault = DummyVault()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(trader, vault))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        path = "/api/paper/enable"
        body = b"{}"
        conn.request("POST", path, body=body, headers={
            "Origin": "https://evil.example",
            "Content-Type": "application/json",
            "X-KalshiEdge-Action": "autotrader",
        })
        response = conn.getresponse()
        response.read()
        assert response.status == 403
        assert not trader.enabled()

        conn.request("POST", path, body=body, headers={
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "Content-Type": "application/json",
            "X-KalshiEdge-Action": "autotrader",
        })
        response = conn.getresponse()
        response.read()
        assert response.status == 200
        assert trader.enabled()

        secret = "PEM_SECRET_TEST_STRING"
        conn.request("POST", "/api/credentials",
                     body=json.dumps({"api_key_id": "id", "private_key_pem": secret}),
                     headers={
                         "Origin": f"http://127.0.0.1:{server.server_port}",
                         "Content-Type": "application/json",
                         "X-KalshiEdge-Action": "autotrader",
                     })
        response = conn.getresponse()
        text = response.read().decode()
        assert response.status == 200
        assert secret not in text
        assert vault.saved == ("id", secret)

        conn.request("GET", "/api/status")
        response = conn.getresponse()
        text = response.read().decode()
        assert response.status == 200
        assert secret not in text
        assert json.loads(text)["credentials_saved"] is True
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
