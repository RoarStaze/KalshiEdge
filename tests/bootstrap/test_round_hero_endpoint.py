"""Unified HTTP dashboard regression for price hero and forecast continuity."""
import threading

import httpx

from http.server import ThreadingHTTPServer
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.trading.paper import PaperTrader


class _Vault:
    def exists(self):
        return False


def test_trade_route_retains_live_price_hero_without_autotrader(tmp_path):
    handler = _make_handler(
        tmp_path,
        paper_trader=PaperTrader(tmp_path),
        credential_vault=_Vault(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            url = f"http://127.0.0.1:{server.server_port}"
            response = client.get(url + "/train")
            assert response.status_code == 200
            assert response.text.count('id="round-price-hero"') == 1
            assert response.text.index('class="layout"') < response.text.index('id="round-price-hero"')
            assert response.text.index('id="lock-banner"') < response.text.index('id="round-price-hero"') < response.text.index('id="direction"')
            assert response.text.count('id="round-countdown"') == 0
            assert ".forecast .round-price-hero" in response.text
            assert response.text.count('id="autotrader"') == 0
            assert 'id="hero-current"' in response.text
            assert 'id="hero-strike"' in response.text
            assert 'id="hero-countdown"' in response.text
            assert "renderRoundPriceHero(data);" in response.text
            assert "setInterval(refreshLive,5000)" in response.text
            assert "setInterval(clock,250)" in response.text
            assert "renderLive(await response.json())" in response.text
            live = client.get(url + "/api/live")
            assert live.status_code == 200
            assert "latest_prediction" in live.json()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
