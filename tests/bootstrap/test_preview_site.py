"""The 'kalshiedge test' website is isolated, synthetic and never trading."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import httpx

from kalshi_edge.bootstrap.preview_site import make_preview_handler, run_test_preview


def _serve(handler):
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    return server,worker


def test_preview_uses_current_real_pages_with_a_clear_synthetic_banner():
    server,worker=_serve(make_preview_handler())
    try:
        with httpx.Client(timeout=5,trust_env=False) as client:
            base=f"http://127.0.0.1:{server.server_port}"
            assert client.get(base+"/").headers["location"]=="/train"
            for path in ("/trade","/train","/history","/api"):
                r=client.get(base+path)
                assert r.status_code==200
                assert 'data-preview="true"' in r.text
                assert f'data-page="{path[1:]}"' in r.text
                assert "TEST PREVIEW" in r.text
                assert "simulated" in r.text.lower()
                assert 'id="train-now"' in r.text
                assert 'id="manual-submit"' in r.text
                assert 'id="api-credentials-form"' in r.text
                assert "pointer-events:none" in r.text
                assert "Cache-Control" in r.headers
            assert client.get(base+"/random-path").status_code==404
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)


def test_preview_responds_with_synthetic_ui_data_without_live_state():
    server,worker=_serve(make_preview_handler())
    try:
        with httpx.Client(timeout=5,trust_env=False) as client:
            base=f"http://127.0.0.1:{server.server_port}"
            health=client.get(base+"/api/health").json()
            assert health["preview"] is True
            assert health["service"] != "unified-dashboard"
            run=client.get(base+"/api/training").json()
            assert run["preview"] is True
            assert run["running"] is True
            assert run["run"]["stage"]=="FITTING"
            assert run["run"]["fit_steps_total"]>run["run"]["fit_steps_completed"]
            assert run["forward_480"]["required_markets"]>0
            assert "current_model" in run
            progress=client.get(base+"/api/training/progress").json()
            assert progress["run"]["stage"]=="FITTING"
            live=client.get(base+"/api/live").json()
            assert live["preview"] is True
            assert live["market_ticker"].startswith("KXBTC15M-DEMO")
            history=client.get(base+"/api/history").json()
            assert history["preview"] is True
            assert isinstance(history["forecasts"],list)
            assert isinstance(history["summary"]["correct"],int)
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)


def test_preview_rejects_every_write_even_with_valid_live_headers():
    server,worker=_serve(make_preview_handler())
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5,trust_env=False) as client:
            for path,action in (
                ("/api/training/start","train"),("/api/training/pause","train"),
                ("/api/training/resume","train"),("/api/manual-trade/order","manual-trade"),
                ("/api/armed-trade/arm","armed-trade"),
                ("/api/autotrader/credentials","autotrader"),
                ("/api/autotrader/credentials/delete","autotrader"),
                ("/api/autotrader/paper/enable","autotrader"),
            ):
                r=client.post(base+path,json={},headers={
                    "Origin":base, "X-KalshiEdge-Action":action,
                    "Sec-Fetch-Site":"same-origin",
                })
                assert r.status_code==403,(path,r.text)
                assert "preview" in r.json()["error"].lower()
            assert client.get(base+"/api/manual-trade/market").status_code==403
            assert client.get(base+"/api/armed-trade/status").status_code==403
            assert client.get(base+"/api/autotrader/status").json()["credentials_supported"] is False
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)


def test_preview_may_only_read_allowlisted_background_image(tmp_path):
    image=tmp_path/"kalshiedge-neon-city.webp"
    image.write_bytes(b"RIFF"+(8).to_bytes(4,"little")+b"WEBP"+b"VP8 "+b"\x00"*4)
    server,worker=_serve(make_preview_handler(asset_path=image))
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5,trust_env=False) as client:
            r=client.get(base+"/assets/kalshiedge-neon-city.webp")
            assert r.status_code==200
            assert r.content==image.read_bytes()
            assert client.get(base+"/assets/../private.pem").status_code==404
            assert client.get(base+"/assets/private.pem").status_code==404
            assert client.get(base+"/api/training",headers={"Host":"attacker.example"}).status_code==403
        assert sorted(x.name for x in tmp_path.iterdir())==["kalshiedge-neon-city.webp"]
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)


def test_preview_refuses_live_port_without_starting_anything(capsys):
    assert run_test_preview(port=8899,open_browser=False)==2
    assert "8899" in capsys.readouterr().err
