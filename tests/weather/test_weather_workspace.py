"""WeatherEdge market picker, isolated pages and read-only data integration."""
from __future__ import annotations

import sqlite3
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.bootstrap.site_pages import render_market_selector
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.preview_site import make_preview_handler
from kalshi_edge.weather.research_ui import render_weather_page
from kalshi_edge.weather.status import read_weather_status


@pytest.mark.parametrize("market", ["bitcoin", "gold", "weather"])
def test_market_picker_includes_weather_and_preserves_selected_market(market):
    markup = render_market_selector(market)
    assert 'href="/trade"' in markup
    assert 'href="/gold"' in markup
    assert 'href="/weather"' in markup
    assert "Weather" in markup
    assert f'data-market-selected="{market}"' in markup
    assert markup.count('aria-current="true"') == 1


@pytest.mark.parametrize("section", ["forecast", "train", "history", "data"])
def test_weather_pages_use_isolated_paper_only_navigation(section):
    page = render_weather_page(section)
    assert 'data-market="weather"' in page
    assert 'data-market-selected="weather"' in page
    assert f'data-weather-section="{section}"' in page
    assert 'WeatherEdge AI' in page
    assert 'PAPER ONLY' in page
    for path in ("/weather", "/weather/train", "/weather/history", "/weather/data"):
        assert f'href="{path}"' in page
    if section=="history":
        assert '/api/weather/hourly/history' in page
        assert '/api/weather/hourly/status' not in page
    else:
        assert '/api/weather/hourly/status' in page
    assert '/api/live' not in page
    assert '/api/manual-trade/' not in page
    assert '/api/armed-trade/' not in page
    assert 'id="manual-submit"' not in page
    assert 'id="train-now"' not in page
    assert '<form' not in page.lower()


def test_weather_status_reads_real_collector_db_but_never_invents_accuracy(tmp_path):
    root = tmp_path / "bootstrap"
    db = tmp_path / "weather" / "weatheredge.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY,station TEXT,source TEXT,event_time TEXT,received_at TEXT,payload TEXT)")
        conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                     ("KORD", "HRRR", "2026-11-19T07:00:00+00:00", "2026-11-19T07:12:00+00:00", "{}"))
        conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                     ("KORD", "METAR", "2026-11-19T07:10:00+00:00", "2026-11-19T07:14:00+00:00", "{}"))
        conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                     ("KDEN", "NBM", "2026-11-19T07:00:00+00:00", "2026-11-19T07:21:00+00:00", "{}"))
    status = read_weather_status(root)
    assert status["storage_state"] == "READY"
    assert status["mode"] == "PAPER_ONLY"
    assert status["real_orders_enabled"] is False
    assert status["forecast_available"] is False
    assert status["settlement_verification"] == "NOT_CERTIFIED"
    assert status["sample_count"] == 3
    stations = {entry["station"]: entry for entry in status["stations"]}
    assert stations["KORD"]["sources"]["HRRR"]["count"] == 1
    assert stations["KORD"]["sources"]["METAR"]["count"] == 1
    assert stations["KDEN"]["sources"]["NBM"]["count"] == 1


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_weather_status_fails_closed_on_missing_or_corrupt_database(tmp_path, state):
    root = tmp_path / "bootstrap"
    if state == "corrupt":
        db = tmp_path / "weather" / "weatheredge.sqlite3"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"not a database")
    status = read_weather_status(root)
    assert status["storage_state"] == ("NOT_FOUND" if state == "missing" else "ERROR")
    assert status["forecast_available"] is False
    assert status["real_orders_enabled"] is False
    assert status["stations"] == []


@pytest.mark.parametrize("preview", [False, True])
def test_live_and_preview_routes_keep_weather_read_only(tmp_path, preview):
    handler = (make_preview_handler() if preview else
               _make_handler(tmp_path / "bootstrap", credential_vault=SimpleNamespace(exists=lambda: True)))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            for path in ("/weather", "/weather/train", "/weather/history", "/weather/data"):
                response = client.get(base + path)
                assert response.status_code == 200, path
                assert 'data-market="weather"' in response.text
                assert 'PAPER ONLY' in response.text
            status = client.get(base + "/api/weather/status")
            assert status.status_code == 200
            assert status.json()["real_orders_enabled"] is False
            assert status.json()["forecast_available"] is False
            assert client.post(base + "/api/weather/order", json={}).status_code in (403, 404)
            assert client.get(base + "/weather/unknown").status_code == 404
            bitcoin = client.get(base + "/train")
            assert bitcoin.status_code == 200
            assert 'data-market-selected="bitcoin"' in bitcoin.text
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
