"""GoldPredict V1 market isolation and routing contracts."""
from http.server import ThreadingHTTPServer
import threading
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.preview_site import make_preview_handler
from kalshi_edge.gold.experiment import gold_experiment_status
from kalshi_edge.gold.research_ui import render_gold_page


def test_market_dropdown_replaces_bitcoin_brand_without_changing_live_ids():
    template = integrate_manual_trade(integrate_round_price_hero(PAGE))
    for section in ("trade", "train", "history", "api"):
        markup = render_site_page(template, section)
        assert '<details class="market-picker"' in markup
        assert 'aria-label="Market selector' in markup
        assert 'href="/trade"' in markup
        assert 'href="/gold"' in markup
        assert 'MARKET</span>' in markup
        assert 'BTC · 15-MINUTE RESEARCH' not in markup
        assert '<div class="name">KALSHIEDGE</div>' not in markup
        assert 'data-market-selected="bitcoin"' in markup
        assert 'id="manual-trade"' in markup
        assert 'id="train-now"' in markup
        assert 'href="/api"' in markup


@pytest.mark.parametrize("section", ["trade", "train", "history", "data"])
def test_gold_pages_keep_experimental_ui_separate_from_bitcoin(section):
    page = render_gold_page(section)
    assert 'data-market="gold"' in page
    assert 'data-market-selected="gold"' in page
    assert 'GoldPredict AI' in page
    assert 'EXPERIMENTAL V1' in page
    assert 'KXGOLD15M' in page
    assert 'Metal.Index.1OZGOLD/USD' in page
    assert 'href="/trade"' in page
    assert 'href="/gold"' in page
    assert f'data-gold-section="{section}"' in page
    assert 'href="/gold/train"' in page
    assert 'href="/gold/history"' in page
    assert 'href="/gold/data"' in page
    assert '/api/live' not in page
    assert '/api/training/start' not in page
    assert '/api/manual-trade/' not in page
    assert '/api/armed-trade/' not in page
    assert 'id="manual-submit"' not in page
    assert 'id="train-now"' not in page
    assert '<form' not in page.lower()


def test_gold_model_readiness_is_explicitly_fail_closed():
    info = gold_experiment_status()
    assert info["series_ticker"] == "KXGOLD15M"
    assert info["settlement_source"] == "Metal.Index.1OZGOLD/USD"
    assert info["experiment"] == "GoldPredict AI — Experimental V1"
    assert info["training_enabled"] is False
    assert info["predictions_enabled"] is False
    assert info["trading_enabled"] is False
    assert info["automated_orders_enabled"] is False
    assert info["readiness"]["checkpoint_verified"] is False
    assert info["readiness"]["settlement_oracle_verified"] is False
    assert info["readiness"]["point_in_time_features_verified"] is False


@pytest.mark.parametrize("preview", [False, True])
def test_gold_routes_and_status_are_read_only_on_live_and_preview(tmp_path, preview):
    if preview:
        handler = make_preview_handler()
    else:
        handler = _make_handler(tmp_path, credential_vault=SimpleNamespace(exists=lambda: True))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            for path in ("/gold", "/gold/train", "/gold/history", "/gold/data"):
                response = client.get(base + path)
                assert response.status_code == 200, path
                assert 'data-market="gold"' in response.text
                assert 'GoldPredict AI' in response.text
            status = client.get(base + "/api/gold/status")
            assert status.status_code == 200
            assert status.json()["trading_enabled"] is False
            assert status.json()["training_enabled"] is False
            assert client.post(base + "/api/gold/train", json={}).status_code in (403, 404)
            assert client.get(base + "/gold/unknown").status_code == 404
            bitcoin = client.get(base + "/train")
            assert bitcoin.status_code == 200
            assert 'data-market-selected="bitcoin"' in bitcoin.text
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
