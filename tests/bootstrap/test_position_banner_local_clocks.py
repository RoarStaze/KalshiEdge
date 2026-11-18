"""Trade header must show actual holding and estimated P/L, not forecast archive status.

All user-facing market timestamps must use the browser's local timezone.
"""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import SCRIPT as MANUAL_SCRIPT
from kalshi_edge.bootstrap.round_price_ui import HERO_SCRIPT, integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page


def test_top_forecast_banner_is_position_only_and_reuses_live_market_snapshot():
    page = render_site_page(integrate_round_price_hero(PAGE), "trade") if False else None
    # The full active trade template includes manual market account reads.
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    page = render_site_page(integrate_manual_trade(integrate_round_price_hero(PAGE)), "trade")
    assert 'id="lock-banner"' in page
    assert 'id="lock-heading"' in page and 'id="lock-description"' in page
    assert "8-MIN FINAL FORECAST ARCHIVED · LIVE MONITOR ON" not in page
    assert "LIVE THROUGH MARKET CLOSE" not in page
    assert "8-MIN RECORD PENDING · LIVE MONITOR ON" not in page
    assert "BOUGHT UP" in page
    assert "BOUGHT DOWN" in page
    assert "UNREALIZED P/L" in page
    assert "kalshiedge:position-snapshot" in page
    assert "position_unrealized_pnl_dollars" in page
    assert 'id="scalp-reversal-risk"' in page
    assert 'id="scalp-flip-alert"' in page
    assert 'id="confidence"' in page


def test_position_banner_receives_market_side_and_pnl_from_existing_account_poll():
    assert 'CustomEvent("kalshiedge:position-snapshot"' in MANUAL_SCRIPT
    assert 'detail:{' in MANUAL_SCRIPT
    assert "position_pnl_status" in MANUAL_SCRIPT
    assert "position_unrealized_pnl_dollars" in MANUAL_SCRIPT
    assert "observed_at_ms:Date.now()" in MANUAL_SCRIPT


def test_clock_uses_computers_local_timezone_without_pinned_eastern_time():
    page = integrate_round_price_hero(PAGE)
    assert "America/New_York" not in page
    assert '" ET · market closes"' not in page
    assert '" ET"' not in HERO_SCRIPT
    assert "timeZoneName:\"short\"" in HERO_SCRIPT
    assert "toLocaleTimeString(undefined" in HERO_SCRIPT
