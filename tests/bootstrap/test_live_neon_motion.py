"""Animated KalshiEdge backdrop and card-border regressions.

The effect is entirely decorative: existing market, training and order
interfaces keep their IDs, controls, and polling behavior.
"""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.site_pages import render_site_page


def render(section):
    base = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(base, section)


def test_animated_background_is_local_and_shared_across_routes():
    for section in ("trade", "train", "history", "api"):
        page = render(section)
        assert 'class="ke-live-scene"' in page
        assert 'aria-hidden="true"' in page
        assert 'class="ke-live-grid"' in page
        assert 'id="ke-live-canvas"' in page
        assert 'class="ke-live-beam"' in page
        assert 'ke-image-breathe' in page
        assert 'ke-beam-sweep' in page
        assert 'url("/assets/kalshiedge-neon-city.webp")' in page
        assert 'pointer-events:none' in page
        assert 'prefers-reduced-motion:reduce' in page
        assert 'background-attachment:fixed' not in page


def test_each_visible_panel_family_has_an_orbiting_green_border():
    page = render("trade")
    assert 'svg.setAttribute("class","ke-border-overlay")' in page
    assert '@keyframes ke-border-travel' in page
    assert 'pathLength' in page
    for selector in (
        '.site-nav', '.card', '.manual-trade',
        '.training-step', '.training-count', '.kpi',
        '.history-section', '.api-card',
        '.hero-price-surface', '.hero-clock-panel',
        '.mt-auto-record', '.mt-inputbox',
    ):
        assert selector in page
    assert 'animation:ke-border-travel' in page
    assert 'animation:none!important' in page


def test_animation_does_not_change_trading_or_training_interface():
    for section in ("trade", "train", "history", "api"):
        page = render(section)
        assert page.count('class="ke-live-scene"') == 1
        assert page.count('id="manual-submit"') == 1
        assert page.count('id="train-now"') == 1
        assert page.count('id="history-rows"') == 1
        assert page.count('id="api-credentials-form"') == 1
        assert page.count('class="site-nav-link') == 4
