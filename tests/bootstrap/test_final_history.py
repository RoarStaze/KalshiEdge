from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import (
    _make_handler,
    _settlement_from_payload,
    _settlement_path,
    read_final_history,
    sync_final_settlements,
)

NS = 1_000_000_000
CLOSE = 1_800_000_900_000_000_000
DUE = CLOSE - 240 * NS
TICKER = "KXBTC15M-26OCT280430-30"
BASE = "https://external-api.kalshi.com/trade-api/v2"


def _forecast(side: str = "ABOVE", ticker: str = TICKER) -> FinalForecastRecord:
    return FinalForecastRecord(
        market_ticker=ticker,
        strike=81043.29,
        scheduled_ts_ns=DUE,
        prediction_ts_ns=DUE + NS // 3,
        seconds_remaining=239.667,
        forecast=side,
        reason="CONFIDENCE_BELOW_69_PERCENT" if side == "SKIP" else None,
        confidence=0.9596 if side != "SKIP" else 0.3,
        directional_probability=0.9798 if side != "SKIP" else 0.65,
        p_yes=0.9798 if side != "SKIP" else 0.65,
        p_no=0.0202 if side != "SKIP" else 0.35,
        model_hash="a" * 64,
        model_version="bootstrap-hybrid-v5",
    )


def _market_payload(ticker: str = TICKER, result: str = "yes", status: str = "finalized", **override) -> dict:
    record = {
        "ticker": ticker,
        "result": result,
        "status": status,
        "close_time": datetime.fromtimestamp(CLOSE / NS, timezone.utc).isoformat(),
        "strike_type": "greater",
        "floor_strike": "81043.29",
        "is_provisional": False,
    }
    record.update(override)
    return {"market": record}


def _record(root: Path, side: str = "ABOVE", ticker: str = TICKER) -> FinalForecastRecord:
    forecast = _forecast(side, ticker)
    saved = FinalForecastWriter(root).save(forecast, market_close_ts_ns=CLOSE)
    assert saved is not None
    return forecast


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scored_history_only_after_official_finalized_result(tmp_path: Path) -> None:
    forecast = _record(tmp_path)
    checked = []

    def handler(request):
        checked.append(request)
        return httpx.Response(200, json=_market_payload())

    with _http(handler) as client:
        early = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 59 * NS, http=client)
        assert early["requests"] == 0
        assert read_final_history(tmp_path)["summary"]["accuracy"] is None

        report = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 61 * NS, http=client)
        assert report["newly_resolved"] == 1
        assert report["requests"] == 1

        again = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 100 * NS, http=client)
        assert again["already_resolved"] == 1
        assert again["requests"] == 0

    assert len(checked) == 1
    assert not any("authorization" in request.headers for request in checked)
    assert not any("kalshi-access-" in key for request in checked for key in request.headers)

    stored = json.loads(_settlement_path(tmp_path, forecast).read_text())
    assert stored["market_ticker"] == TICKER
    assert stored["result"] == "yes"
    assert stored["actual_side"] == "ABOVE"
    history = read_final_history(tmp_path)
    assert history["summary"]["correct"] == 1
    assert history["summary"]["incorrect"] == 0
    assert history["summary"]["accuracy"] == 1.0
    assert history["summary"]["pending_settlement"] == 0
    assert history["forecasts"][0]["verdict"] == "CORRECT"
    assert history["forecasts"][0]["directional_probability"] == pytest.approx(0.9798)


def test_incorrect_result_counts_only_after_settlement(tmp_path: Path) -> None:
    _record(tmp_path, side="ABOVE")
    with _http(lambda request: httpx.Response(200, json=_market_payload(result="no"))) as client:
        sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 80 * NS, http=client)
    history = read_final_history(tmp_path)
    assert history["summary"]["incorrect"] == 1
    assert history["summary"]["correct"] == 0
    assert history["summary"]["accuracy"] == 0.0
    assert history["forecasts"][0]["actual_side"] == "BELOW"
    assert history["forecasts"][0]["verdict"] == "INCORRECT"


def test_skip_is_never_graded_as_correct_or_incorrect(tmp_path: Path) -> None:
    _record(tmp_path, side="SKIP")
    before = read_final_history(tmp_path)
    assert before["summary"]["pending_settlement"] == 1
    assert before["summary"]["skipped"] == 1
    assert before["summary"]["accuracy"] is None

    with _http(lambda request: httpx.Response(200, json=_market_payload(result="no"))) as client:
        sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 80 * NS, http=client)

    after = read_final_history(tmp_path)
    assert after["summary"]["skipped"] == 1
    assert after["summary"]["settled_skips"] == 1
    assert after["summary"]["settled_directional_forecasts"] == 0
    assert after["summary"]["accuracy"] is None
    assert after["summary"]["settled_decision_coverage"] == 0.0
    assert after["forecasts"][0]["verdict"] == "SKIP"
    assert after["forecasts"][0]["actual_side"] == "BELOW"


@pytest.mark.parametrize("changes", [
    {"ticker": "KXBTC15M-OTHER"},
    {"floor_strike": "91043.29"},
    {"strike_type": "less"},
    {"close_time": "2026-01-01T00:00:00Z"},
])
def test_mismatched_market_metadata_cannot_score_a_forecast(tmp_path: Path, changes: dict) -> None:
    _record(tmp_path)
    with _http(lambda request: httpx.Response(200, json=_market_payload(**changes))) as client:
        stats = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 100 * NS, http=client)
    assert stats["newly_resolved"] == 0
    assert stats["errors"] == 1
    assert read_final_history(tmp_path)["summary"]["accuracy"] is None


@pytest.mark.parametrize("changes", [
    {"result": ""},
    {"status": "active"},
    {"status": "determined"},
    {"is_provisional": True},
])
def test_unsettled_or_provisional_market_remains_pending(tmp_path: Path, changes: dict) -> None:
    _record(tmp_path)
    with _http(lambda request: httpx.Response(200, json=_market_payload(**changes))) as client:
        stats = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 100 * NS, http=client)
    assert stats["newly_resolved"] == 0
    assert stats["pending"] == 1
    assert read_final_history(tmp_path)["summary"]["accuracy"] is None


def test_historical_api_fallback_after_current_market_404(tmp_path: Path) -> None:
    _record(tmp_path, side="BELOW")
    requested = []

    def handler(request):
        requested.append(request.url.path)
        if "/historical/" not in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json=_market_payload(result="no"))

    with _http(handler) as client:
        stats = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 100 * NS, http=client)
    assert stats["newly_resolved"] == 1
    assert requested == [
        "/trade-api/v2/markets/" + TICKER,
        "/trade-api/v2/historical/markets/" + TICKER,
    ]
    assert read_final_history(tmp_path)["summary"]["correct"] == 1


def test_api_error_never_turns_pending_into_incorrect(tmp_path: Path) -> None:
    _record(tmp_path)
    with _http(lambda request: httpx.Response(503)) as client:
        stats = sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 100 * NS, http=client)
    assert stats["errors"] == 1
    history = read_final_history(tmp_path)
    assert history["summary"]["incorrect"] == 0
    assert history["summary"]["pending_settlement"] == 1
    assert history["forecasts"][0]["verdict"] == "PENDING"


def test_saved_result_tampering_is_not_accepted(tmp_path: Path) -> None:
    forecast = _record(tmp_path)
    with _http(lambda request: httpx.Response(200, json=_market_payload(result="yes"))) as client:
        sync_final_settlements(tmp_path, BASE, now_ns=CLOSE + 100 * NS, http=client)
    path = _settlement_path(tmp_path, forecast)
    value = json.loads(path.read_text())
    value["actual_side"] = "BELOW"
    path.write_text(json.dumps(value))
    history = read_final_history(tmp_path)
    assert history["summary"]["accuracy"] is None
    assert history["summary"]["pending_settlement"] == 1


def test_localhost_dashboard_and_json_api(tmp_path: Path) -> None:
    _record(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(tmp_path))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with httpx.Client(timeout=5) as client:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            page = client.get(base + "/history")
            assert page.status_code == 200
            assert "Final forecast history" in page.text
            assert "correct + incorrect" in page.text
            api = client.get(base + "/api/history")
            assert api.status_code == 200
            data = api.json()
            assert data["summary"]["total_final_forecasts"] == 1
            assert data["forecasts"][0]["forecast"] == "ABOVE"
            assert data["forecasts"][0]["verdict"] == "PENDING"
            live = client.get(base + "/api/live")
            assert live.status_code == 200
            assert live.json()["status"] == "WAITING_FOR_PREDICTOR"
            assert live.json()["final_forecast"] is None
            health = client.get(base + "/api/health")
            assert health.status_code == 200
            assert health.json()["app"] == "kalshiedge"
            assert health.json()["service"] == "unified-dashboard"
            assert client.get(base + "/orders").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
