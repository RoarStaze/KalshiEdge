"""Unified dashboard contract: AutoTrader runs on the existing 8899 website."""
from __future__ import annotations

import httpx
import threading
from http.server import ThreadingHTTPServer

from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.trading.paper import PaperTrader


class FakeVault:
    def __init__(self):
        self.saved = None
    def exists(self):
        return self.saved is not None
    def save(self, key, pem):
        self.saved = (key, pem)
    def delete(self):
        self.saved = None


def test_unified_dashboard_contains_autotrader_and_same_origin_controls(tmp_path):
    vault = FakeVault()
    trader = PaperTrader(tmp_path)
    handler = _make_handler(tmp_path, paper_trader=trader, credential_vault=vault)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5, trust_env=False) as client:
            main = client.get(origin + "/")
            assert main.status_code == 200
            assert "Final forecast history" in main.text
            assert "AutoTrader" in main.text
            assert 'id="autotrader"' in main.text
            assert 'id="autotrader-credentials"' in main.text
            assert '127.0.0.1:8898' not in main.text

            status = client.get(origin + "/api/autotrader/status")
            assert status.status_code == 200
            assert status.json()["mode"] == "PAPER_ONLY"
            assert status.json()["enabled"] is False
            assert status.json()["credentials_saved"] is False

            def post(path, payload, origin_header=origin):
                return client.post(
                    origin + path, json=payload,
                    headers={
                        "Origin": origin_header,
                        "X-KalshiEdge-Action": "autotrader",
                        "Sec-Fetch-Site": "same-origin",
                    }
                )

            denied = post("/api/autotrader/paper/enable", {}, "http://malicious.example")
            assert denied.status_code == 403
            assert trader.enabled() is False
            accepted = post("/api/autotrader/paper/enable", {})
            assert accepted.status_code == 200
            assert trader.enabled() is True

            saved = post("/api/autotrader/credentials", {
                "api_key_id": "test-id",
                "private_key_pem": "FAKE_SECRET_FROM_USER",
            })
            assert saved.status_code == 200
            assert vault.saved == ("test-id", "FAKE_SECRET_FROM_USER")
            status = client.get(origin + "/api/autotrader/status")
            assert status.json()["credentials_saved"] is True
            assert "FAKE_SECRET_FROM_USER" not in status.text

            stopped = post("/api/autotrader/paper/disable", {})
            assert stopped.status_code == 200
            assert trader.enabled() is False
            removed = post("/api/autotrader/credentials/delete", {})
            assert removed.status_code == 200
            assert vault.saved is None
    finally:
        trader.enable(False)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_existing_training_action_is_not_broken_by_autotrader_routes(tmp_path):
    handler = _make_handler(tmp_path, paper_trader=PaperTrader(tmp_path),
                            credential_vault=FakeVault())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5, trust_env=False) as client:
            denied = client.post(origin + "/api/training/start", json={},
                                 headers={"Origin": origin,
                                          "X-KalshiEdge-Action": "autotrader"})
            assert denied.status_code == 403
            assert client.get(origin + "/api/history").status_code == 200
            assert client.get(origin + "/api/health").json()["service"] == "unified-dashboard"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
