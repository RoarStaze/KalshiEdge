"""Rainbow effects are confined to the OUTER KalshiEdge panels."""
from __future__ import annotations

import re

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.site_pages import render_site_page


def render(section):
    template = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(template, section)


# Those are the actual page-level boxed surfaces. All other bordered
# descendants must retain their existing, non-animated styling.
OUTER = {
    ".site-nav",
    ".forecast",
    ".context>.manual-trade",
    ".training-card",
    ".history-view .card",
    ".api-card",
}


def test_only_outer_panels_are_selected_for_rainbow_border_on_all_pages():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        selectors = re.search(r'const boxSelector=\[(.*?)\]\.join\(","\)', html, re.S)
        assert selectors, "Missing targeted outer-panel selector"
        found = set(re.findall(r'"([^"]+)"', selectors.group(1)))
        assert found == OUTER
        assert 'closest(".ke-neon-target")' in html


def test_full_color_cycle_is_drawn_on_card_border_box_not_inner_svg():
    html = render("trade")
    assert "@property --ke-rainbow-angle" in html
    assert "conic-gradient(from var(--ke-rainbow-angle)" in html
    assert "padding-box" in html and "border-box" in html
    assert "border:2px solid transparent!important" in html
    assert "@keyframes ke-rainbow-cycle" in html
    assert "--ke-rainbow-angle:360deg" in html
    assert "ke-border-overlay" not in html
    assert "createElementNS" not in html


def test_cinematic_background_keeps_no_extra_particles():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        assert 'id="ke-live-canvas"' not in html
        assert "ke-live-beam" not in html
        assert "ke-live-grid" not in html
        assert "ke-live-canvas" not in html
        assert "tickVerifiedMarketCounter" in html  # training UI motion, not backdrop
        assert 'url("/assets/kalshiedge-neon-city.webp")' in html


def test_order_training_history_and_api_ids_unchanged():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        for target in ("manual-submit", "train-now", "history-rows", "api-credentials-form"):
            assert html.count('id="' + target + '"') == 1
        assert html.count('class="site-nav-link') == 4
        assert "pointer-events:none" in html


def test_rainbow_is_the_real_outer_card_border_not_padding_box_overlay():
    html = render("trade")
    assert ".ke-neon-target" in html
    assert "conic-gradient(from var(--ke-rainbow-angle)" in html
    assert "border:2px solid transparent!important" in html
    assert "ke-border-overlay" not in html


def test_outer_nav_and_trade_panels_keep_full_color_cycling_outline():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        assert "border-box" in html
        assert "ke-rainbow-cycle" in html
        assert 'if(node.parentElement?.closest(".ke-neon-target"))return;' in html
