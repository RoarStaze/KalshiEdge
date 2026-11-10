"""Cinematic KalshiEdge backdrop stays local, readable, and secure."""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import httpx

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.site_pages import render_site_page


BACKGROUND_URL = "/assets/kalshiedge-neon-city.webp"


def _full_page(section: str) -> str:
    composed = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(composed, section)


def test_cinematic_background_applies_to_all_pages_and_keeps_center_readable():
    for section in ("trade", "train", "history", "api"):
        html = _full_page(section)
        assert f'url("{BACKGROUND_URL}")' in html
        assert 'body::before' in html
        assert 'pointer-events:none' in html
        assert "background-size:cover,cover,cover" in html
        assert "background-attachment:fixed" not in html
        assert 'prefers-reduced-motion' in html
        assert 'id="train-now"' in html
        assert 'id="manual-submit"' in html
    trade = _full_page("trade")
    assert 'body[data-page="trade"] .forecast' in trade
    assert 'body[data-page="trade"] .context>.manual-trade' in trade
    # No third-party CDNs or remote images are needed for this design.
    assert "https://images." not in trade


def test_background_route_serves_only_local_webp_from_bootstrap_storage(tmp_path):
    root = tmp_path
    image = root / "assets" / "kalshiedge-neon-city.webp"
    image.parent.mkdir()
    data = b"RIFF" + (8).to_bytes(4, "little") + b"WEBP" + b"VP8 " + b"\x00\x00\x00\x00"
    image.write_bytes(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base=f"http://127.0.0.1:{srv.server_port}"
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.get(base+BACKGROUND_URL)
            assert response.status_code == 200
            assert response.content == data
            assert response.headers["content-type"] == "image/webp"
            assert response.headers.get("cache-control") == "public, max-age=3600"
            train = client.get(base+"/train")
            assert train.status_code == 200
            assert "img-src 'self'" in train.headers["content-security-policy"]
            assert BACKGROUND_URL in train.text
            assert client.get(base+"/assets/../training_runs/current.json").status_code == 404
            assert client.get(base+"/assets/private.json").status_code == 404
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=3)


def test_background_asset_optional_and_invalid_media_rejected(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(tmp_path))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base=f"http://127.0.0.1:{srv.server_port}"
        with httpx.Client(timeout=5, trust_env=False) as client:
            assert client.get(base+BACKGROUND_URL).status_code == 404
            # The dashboard is still usable before installing the local file.
            assert client.get(base+"/train").status_code == 200
            dest=tmp_path/"assets"/"kalshiedge-neon-city.webp"
            dest.parent.mkdir()
            dest.write_bytes(b"not a real image")
            assert client.get(base+BACKGROUND_URL).status_code != 200
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=3)
