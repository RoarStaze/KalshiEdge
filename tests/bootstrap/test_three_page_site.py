"""Modern three-route dashboard contract.

Route separation is visual and navigational; existing live IDs stay in the DOM
for the legacy forecast/training/history renderers. No live trading performed.
"""
from http.server import ThreadingHTTPServer
import threading
import httpx

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page
from kalshi_edge.bootstrap.final_history import _make_handler


def test_navigation_has_four_real_paths_and_active_link():
    template=integrate_manual_trade(integrate_round_price_hero(PAGE))
    for section in ("trade", "train", "history", "api"):
        page=render_site_page(template,section)
        assert f'data-page="{section}"' in page
        assert f'<title>KalshiEdge · {section.title()}</title>' in page
        assert page.count('class="site-nav-link') == 4
        for other in ("trade","train","history","api"):
            assert f'href="/{other}"' in page
        assert f'aria-current="page">{section.title()}</a>' in page
        assert 'role="navigation"' not in page
        assert 'aria-label="Primary navigation"' in page


def test_trade_only_shows_forecast_and_manual_trade_cards():
    page=render_site_page(integrate_manual_trade(integrate_round_price_hero(PAGE)),"trade")
    assert 'id="manual-trade"' in page
    assert 'class="card forecast"' in page
    assert 'id="round-price-hero"' in page
    assert 'body[data-page="trade"] .training-card' in page
    assert 'body[data-page="trade"] .history-view' in page
    assert '.context>.context-header,.context>.health{display:none!important}' in page
    assert "id=\"autotrader\"" not in page
    assert 'AutoTrader (paper)' not in page
    assert "127.0.0.1:8898" not in page


def test_real_money_trade_page_does_not_claim_to_be_read_only():
    page=render_site_page(integrate_manual_trade(integrate_round_price_hero(PAGE)),"trade")
    assert "Read-only research tool" not in page
    assert "Trading is manual and uses real money" in page


def test_train_and_history_have_exclusive_visible_sections():
    template=integrate_manual_trade(integrate_round_price_hero(PAGE))
    train=render_site_page(template,"train")
    hist=render_site_page(template,"history")
    assert '<section class="card training-card"' in train
    assert 'id="train-now"' in train
    assert 'body[data-page="train"] .layout' in train
    assert 'body[data-page="train"] .history-view' in train
    assert 'aria-label="Final Forecast history"' in hist
    assert 'id="history-rows"' in hist
    assert 'body[data-page="history"] .layout' in hist
    assert 'body[data-page="history"] .training-card' in hist


def test_hidden_pages_do_not_run_manual_trade_account_poller():
    page=render_site_page(integrate_manual_trade(integrate_round_price_hero(PAGE)),"history")
    assert 'document.body.dataset.page !== "trade"' in page
    assert 'const sitePage=document.body.dataset.page;' in page
    assert 'if(sitePage==="history")' in page
    assert 'if(sitePage==="train")' in page


def test_real_routes_and_existing_api_remain_available(tmp_path):
    handler=_make_handler(tmp_path)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        root=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5,trust_env=False) as client:
            for path,section in (("/","trade"),("/trade","trade"),
                                 ("/train","train"),("/history","history")):
                r=client.get(root+path)
                assert r.status_code==200
                assert f'data-page="{section}"' in r.text
                assert "id=\"autotrader\"" not in r.text
                assert 'id="manual-trade"' in r.text
            assert client.get(root+"/api/history").status_code==200
            assert client.get(root+"/api/health").status_code==200
            assert client.get(root+"/unknown-page").status_code==404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
