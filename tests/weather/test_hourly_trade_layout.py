"""Approved Bitcoin-style Weather Trade presentation (no weather orders)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from kalshi_edge.weather.hourly_ui import render_hourly_page


def test_weather_trade_matches_bitcoin_two_column_hierarchy():
    html=render_hourly_page("forecast","")
    assert 'data-weather-section="forecast"' in html
    assert 'class="wx-trade-grid"' in html
    assert 'class="wx-trade-forecast wx-panel"' in html
    assert 'class="wx-trade-quote wx-panel"' in html
    assert "THIS HOUR’S FORECAST" in html
    assert "TEMP TO BEAT" in html
    assert "CURRENT INDEX" in html
    assert "ROUND CLOSES IN" in html
    assert 'id="wx-hourly-close-clock"' in html
    assert 'id="wx-hourly-direction"' in html
    assert 'id="wx-hourly-confidence"' in html
    assert 'id="wx-hourly-progress"' in html
    assert "Forecast probability" in html
    assert "REVERSAL RISK" in html
    assert "FLIP ALERTS" in html
    assert 'id="wx-hourly-flips"' in html
    assert "PAPER MARKET PREVIEW" in html
    assert 'id="wx-paper-yes"' in html
    assert 'id="wx-paper-no"' in html
    assert 'id="wx-paper-amount"' in html
    assert 'id="wx-paper-cost"' in html
    assert 'id="wx-paper-payout"' in html
    assert 'id="wx-paper-order-disabled"' in html
    assert 'id="wx-hourly-city"' in html
    assert 'id="wx-hourly-ladder"' in html


def test_weather_trade_never_claims_real_execution_or_fake_balances():
    html=render_hourly_page("forecast","")
    assert "PAPER ONLY" in html
    assert "Kalshi Weather Index" in html
    assert "NOT VALIDATED" in html
    assert "Weather orders disabled" in html
    assert "before fees" in html.lower()
    assert 'id="wx-paper-order-disabled" type="button" disabled' in html
    assert "LIVE ACCOUNT" not in html
    assert "Buy with 1-Click" not in html
    assert "8-MIN RECORD SAVED" not in html
    assert "3°F (+4.5%)" not in html
    assert "72°F" not in html
    assert 'id="wx-hourly-confidence">74%' not in html  # CSS can contain a 74% gradient stop
    assert "live weather orders</span><strong>disabled" not in html.lower()
    assert "/api/manual-trade/" not in html
    assert 'type="submit"' not in html


def test_forecast_navigation_and_technical_evidence_are_preserved():
    html=render_hourly_page("forecast","")
    for fragment in ("/weather/train","/weather/history","/weather/data",
                     "Trade","Train","History","Feeds",
                     'id="wx-hourly-interval"',
                     'id="wx-hourly-nowcast-scores"',
                     'id="wx-physical-adaptive-metrics"',
                     'id="wx-hourly-diagnostic"',
                     "source-matched"):
        # "source-matched" is the V9 calibration telemetry term.
        assert fragment in html
    assert 'id="wx-hm-matched"' in html  # Legacy metrics remain in technical details
    assert html.index('id="wx-hm-matched"') > html.index("Forecast uncertainty and technical details")
    assert html.count('class="wx-trade-forecast wx-panel"')==1
    assert html.count('class="wx-trade-quote wx-panel"')==1
    assert "wx-trade-grid{display:grid;grid-template-columns:" in html
    assert "@media(max-width:920px)" in html
    assert "row.status" in html
    assert "quote_received_at" in html
    assert "close_time_utc" in html
    for section in ("train","data"):
        other=render_hourly_page(section,"")
        assert 'class="wx-trade-grid"' not in other
        assert "/api/weather/hourly/calibration" in other
    history=render_hourly_page("history","")
    assert 'class="wx-trade-grid"' not in history
    assert "/api/weather/hourly/history" in history
    assert "/api/weather/hourly/calibration" not in history


def test_weather_trade_preview_only_uses_fresh_verified_ask():
    html=render_hourly_page("forecast","")
    assert 'activeQuoteStatus==="VALID_ASK"' in html
    assert "activeQuoteReceivedMs" in html
    assert "serverClockBiasMs" in html
    assert "function updatePaperPreview(" in html
    assert "paperOnly" in html
    assert "Number.isFinite" in html
    assert 'setInterval(tickMarketClose,1000)' in html


def test_weather_trade_javascript_parses_when_node_is_available():
    node=shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    html=render_hourly_page("forecast","")
    scripts=html.split("<script>")[1:]
    for raw in scripts:
        script=raw.split("</script>",1)[0]
        result=subprocess.run([node,"--check"],input=script,
                              capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
