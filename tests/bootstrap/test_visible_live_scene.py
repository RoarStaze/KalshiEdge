"""Live cinematic atmosphere and perimeter-tracer regression tests."""
from __future__ import annotations

from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page


def page(name: str) -> str:
    base = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(base, name)


def test_every_route_installs_the_same_visible_live_scene():
    for name in ("trade", "train", "history", "api"):
        html = page(name)
        assert html.count('id="ke-live-canvas"') == 1
        assert html.count('class="ke-live-scene"') == 1
        assert 'aria-hidden="true"' in html
        assert 'ke-beam-sweep' in html
        assert 'ke-grid-scroll' in html
        assert 'getContext("2d"' in html
        assert 'requestAnimationFrame' in html
        assert 'document.visibilityState' in html
        assert 'prefers-reduced-motion: reduce' in html
        assert 'url("/assets/kalshiedge-neon-city.webp")' in html


def test_every_existing_panel_gets_a_real_rounded_perimeter_runner():
    html = page("trade")
    assert 'createElementNS(svgNS,"svg")' in html
    assert 'createElementNS(svgNS,"rect")' in html
    assert 'pathLength' in html
    assert 'stroke-dasharray:19 81' in html
    assert 'stroke-dashoffset:-100' in html
    assert 'ResizeObserver' in html
    assert 'MutationObserver' in html
    assert 'ke-neon-runner' in html
    for cls in (
        ".site-nav", ".card", ".manual-trade", ".training-step",
        ".training-count", ".kpi", ".history-section", ".api-card",
        ".api-status", ".hero-price-surface", ".hero-clock-panel",
        ".mt-auto-record", ".mt-inputbox", ".mt-pills",
    ):
        assert cls in html


def test_unobstructed_buttons_and_original_live_data_routes():
    for name in ("trade", "train", "history", "api"):
        html = page(name)
        assert 'pointer-events:none' in html
        assert html.count('id="manual-submit"') == 1
        assert html.count('id="train-now"') == 1
        assert html.count('id="history-rows"') == 1
        assert html.count('id="api-credentials-form"') == 1
        assert html.count('class="site-nav-link') == 4
        assert 'prefers-reduced-motion:reduce' in html
        assert 'hidden' in html
