"""Continuous scalping display is a read-only monitor, not a live executor."""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import httpx

from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.live_dashboard import read_live_dashboard
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.trading.paper import PaperTrader

NS = 1_000_000_000
CLOSE = 1_800_000_900_000_000_000
TICKER = "KXBTC15M-TEST"


def test_live_api_provides_fresh_risk_without_changing_final_forecast(tmp_path):
    now_ns = CLOSE - 250 * NS
    for i in range(18):
        at = now_ns - (17 - i) * NS
        record = LivePredictionRecord(
            prediction_ts_ns=at,
            market_ticker=TICKER,
            status="OK", feed_quality=FeedQuality(healthy=True),
            model_hash="a" * 64,
            model_version="bootstrap-hybrid-v5",
            strike=60_000.0,
            seconds_remaining=(CLOSE - at) / NS,
            p_yes=.78, p_no=.22,
            predicted_side="ABOVE",
            kalshi_yes_bid=.71, kalshi_yes_ask=.73,
        )
        day = datetime.fromtimestamp(at / NS, timezone.utc).date().isoformat()
        path = tmp_path / "predictions" / day / "predictions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(record.model_dump_json() + "\n")
    response = read_live_dashboard(tmp_path, now_ns=now_ns)
    assert response["status"] == "LIVE"
    assert response["final_forecast"] is None
    assert response["reversal_risk"]["level"] == "LOW"
    assert response["reversal_risk"]["live_orders_enabled"] is False


class Vault:
    def exists(self):
        return True


def test_trade_card_displays_flip_alert_below_reversal_risk_and_uses_live_probability(tmp_path):
    handler = _make_handler(tmp_path, paper_trader=PaperTrader(tmp_path), credential_vault=Vault())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            base = f"http://127.0.0.1:{server.server_port}"
            page = client.get(base + "/trade")
            assert page.status_code == 200
            html = page.text
            assert html.count('id="scalp-reversal-risk"') == 1
            assert html.count('id="scalp-flip-alert"') == 1
            assert html.index('id="record-details"') < html.index('id="scalp-reversal-risk"') < html.index('id="scalp-flip-alert"')
            assert 'const side=p?.status==="OK"?p.predicted_side:null;' in html
            assert 'const sideProbability=p?.status==="OK"?' in html
            assert 'const sideProbability=locked?f.directional_probability:' not in html
            assert 'text("detail-probability",pct(locked?f.directional_probability:sideProbability));' in html
            assert 'renderScalpMonitor(data);' in html
            assert "8-MIN RECORD SAVED" in html
            api = client.get(base + "/api/live")
            assert api.json()["reversal_risk"]["level"] == "UNAVAILABLE"
            if shutil.which("node"):
                for script in re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html, re.I):
                    checked = subprocess.run(["node", "--check"], input=script, text=True, capture_output=True, timeout=10)
                    assert checked.returncode == 0, checked.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_monitor_stays_available_after_prediction_log_exceeds_tail_limit(tmp_path):
    """The monitor must discard a partial line, not the entire 512-KiB tail."""
    now_ns = CLOSE - 250 * NS
    day = datetime.fromtimestamp(now_ns / NS, timezone.utc).date().isoformat()
    path = tmp_path / "predictions" / day / "predictions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        # A partial, invalid historic line in the tail mimics a long-running
        # predictor file. It must not obscure fresh, valid predictions.
        file.write("{" + "x" * (512 * 1024 + 200) + "\n")
        for i in range(20):
            at = now_ns - (19 - i) * NS
            record = LivePredictionRecord(
                prediction_ts_ns=at, market_ticker=TICKER,
                status="OK", feed_quality=FeedQuality(healthy=True),
                model_hash="a" * 64, model_version="bootstrap-hybrid-v5",
                strike=60_000.0, seconds_remaining=(CLOSE - at) / NS,
                p_yes=.78, p_no=.22, predicted_side="ABOVE",
                kalshi_yes_bid=.71, kalshi_yes_ask=.73,
            )
            file.write(record.model_dump_json() + "\n")
    assert path.stat().st_size > 512 * 1024
    result = read_live_dashboard(tmp_path, now_ns=now_ns)
    assert result["status"] == "LIVE"
    assert result["latest_prediction"]["prediction_ts_ns"] == now_ns
    assert result["reversal_risk"]["level"] == "LOW"
    assert result["reversal_risk"]["observation_count"] >= 8
