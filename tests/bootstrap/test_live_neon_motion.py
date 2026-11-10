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


def test_entire_border_is_rainbow_and_color_animation_not_short_dash():
    html = render("trade")
    assert "ke-neon-runner" in html
    assert "ke-rainbow-gradient" in html
    assert "stroke-dasharray:none" in html
    assert "stroke-dasharray:16 84" not in html
    assert "stroke-dashoffset" not in html
    assert "@keyframes ke-rainbow-cycle" in html
    assert "hue-rotate(0deg)" in html
    assert "hue-rotate(360deg)" in html
    assert 'setAttribute("viewBox"' in html


def test_cinematic_background_keeps_no_extra_particles():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        assert 'id="ke-live-canvas"' not in html
        assert "ke-live-beam" not in html
        assert "ke-live-grid" not in html
        assert "requestAnimationFrame" not in html
        assert 'url("/assets/kalshiedge-neon-city.webp")' in html


def test_order_training_history_and_api_ids_unchanged():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        for target in ("manual-submit", "train-now", "history-rows", "api-credentials-form"):
            assert html.count('id="' + target + '"') == 1
        assert html.count('class="site-nav-link') == 4
        assert "pointer-events:none" in html


def test_rainbow_svg_is_aligned_to_true_outer_border_box():
    """Old 100% width/top=0 was relative to the padding box, not border edges."""
    html = render("trade")
    assert 'parseFloat(css.borderLeftWidth)' in html
    assert 'parseFloat(css.borderTopWidth)' in html
    assert 'svg.style.left=' in html
    assert 'svg.style.top=' in html
    assert 'svg.style.width=' in html
    assert 'svg.style.height=' in html
    assert 'const inset=strokeWidth/2' in html
    assert 'const inset=2.5;' not in html


def test_outer_nav_and_trade_panels_keep_full_color_cycling_outline():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        assert "stroke-dasharray:none" in html
        assert "ke-rainbow-cycle" in html
        assert 'if(node.parentElement?.closest(".ke-neon-target"))return;' in html
