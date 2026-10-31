"""API onboarding and no-scroll Trade layout regression tests.

No Kalshi network calls or real trading. Exercise the actual local HTTP server.
"""
import threading
from http.server import ThreadingHTTPServer

import httpx

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page


class CredentialVault:
    def __init__(self, saved=False):
        self.saved = saved
        self.key = None

    def exists(self):
        return self.saved

    def save(self, key, pem):
        self.key = (key, pem)
        self.saved = True

    def delete(self):
        self.saved = False
        self.key = None


def template():
    return integrate_manual_trade(integrate_round_price_hero(PAGE))


def test_four_link_navigation_and_no_intro_on_trade():
    for section in ("trade", "train", "history", "api"):
        page = render_site_page(template(), section)
        assert page.count('class="site-nav-link') == 4
        for link in ("trade", "train", "history", "api"):
            assert f'href="/{link}"' in page
        assert f'aria-current="page">{section.title()}</a>' in page
    trade = render_site_page(template(), "trade")
    assert 'Trade / Live Markets' not in trade
    assert 'Bitcoin, in real time.' not in trade
    assert 'One live round forecast.' not in trade
    assert 'body[data-page="trade"] .section-intro{display:none' in trade
    assert 'body[data-page="trade"] footer{display:none' in trade


def test_trade_viewport_fills_screen_but_small_screens_keep_content_accessible():
    page = render_site_page(template(), "trade")
    assert 'height:100dvh' in page
    assert 'overflow:hidden' in page
    assert 'min-height:0' in page
    assert 'max-height:760px' in page
    assert 'overflow-y:auto' in page
    assert 'id="round-price-hero"' in page
    assert 'id="manual-submit"' in page


def test_api_is_last_page_with_credential_form_and_no_other_cards():
    page=render_site_page(template(),"api")
    assert 'id="api-credentials-form"' in page
    assert 'id="api-key-id"' in page
    assert 'id="api-private-key"' in page
    assert 'id="api-delete"' in page
    assert 'Save encrypted locally' in page
    assert 'api/autotrader' not in page  # API uses existing authenticated URL through distinct JS string
    assert 'body[data-page="api"] .layout' in page
    assert 'body[data-page="api"] .training-card' in page
    assert 'body[data-page="api"] .history-view' in page
    assert 'document.body.dataset.page !== "trade"' in page
    assert 'autocomplete="off"' in page
    assert "localStorage.setItem" not in page
    assert "sessionStorage.setItem" not in page


def run_http(tmp_path, vault):
    handler = _make_handler(tmp_path, credential_vault=vault)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_root_redirects_to_api_if_missing_and_trade_if_present(tmp_path):
    vault=CredentialVault(False)
    server,thread=run_http(tmp_path,vault)
    try:
        origin=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5,trust_env=False,follow_redirects=False) as client:
            missing=client.get(origin+"/")
            assert missing.status_code==302
            assert missing.headers["location"]=="/api"
            assert client.get(origin+"/trade").headers["location"]=="/api"
            ready_api=client.get(origin+"/api")
            assert ready_api.status_code==200
            assert 'id="api-credentials-form"' in ready_api.text
            vault.save("fake-key-id","fake-private-key")
            detected=client.get(origin+"/")
            assert detected.status_code==302
            assert detected.headers["location"]=="/trade"
            assert client.get(origin+"/trade").status_code==200
            assert client.get(origin+"/train").status_code==200
            assert client.get(origin+"/history").status_code==200
            assert client.get(origin+"/api").status_code==200
            vault.delete()
            assert client.get(origin+"/").headers["location"]=="/api"
            assert client.get(origin+"/api/health").status_code==200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_api_save_requires_existing_same_origin_protection(tmp_path):
    vault=CredentialVault(False)
    server,thread=run_http(tmp_path,vault)
    try:
        origin=f"http://127.0.0.1:{server.server_port}"
        path="/api/autotrader/credentials"
        with httpx.Client(timeout=5,trust_env=False) as client:
            payload={"api_key_id":"test-key","private_key_pem":"test-pem"}
            without_origin=client.post(origin+path,json=payload)
            assert without_origin.status_code==403
            cross_site=client.post(origin+path,json=payload,headers={
                "Origin":"https://evil.example",
                "X-KalshiEdge-Action":"autotrader",
                "Sec-Fetch-Site":"cross-site",
            })
            assert cross_site.status_code==403
            assert vault.exists() is False
            valid=client.post(origin+path,json=payload,headers={
                "Origin":origin, "X-KalshiEdge-Action":"autotrader",
                "Sec-Fetch-Site":"same-origin",
            })
            assert valid.status_code==200
            assert vault.exists() is True
            assert "test-pem" not in client.get(origin+"/api").text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
