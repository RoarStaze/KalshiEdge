"""No square backdrop-compositing seams at the rainbow card corners.

The neon stroke itself must stay an animated border-box gradient. Prevent a
second backdrop-filter paint surface from bleeding a straight grey line into
the rounded corner; preserve scrolling and focus/interaction semantics.
"""
from __future__ import annotations

import re

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.site_pages import render_site_page


def rendered(route: str) -> str:
    return render_site_page(
        integrate_api_credentials(
            integrate_manual_trade(integrate_round_price_hero(PAGE))
        ), route,
    )


def test_rainbow_card_has_no_secondary_rectangular_backdrop_surface():
    for route in ("trade", "train", "history", "api"):
        html = rendered(route)
        # The highlighted card gets exactly one curved gradient stroke;
        # the backdrop compositor must not paint a square, faint grey edge.
        neon = re.search(
            r"body\[data-page\] :is\(\.site-nav,\.forecast,"
            r"\.context>\.manual-trade,\.training-card,"
            r"\.history-view \.card,\.api-card\)\.ke-neon-target\s*\{([^}]+)\}",
            html,
        )
        assert neon, "Could not find actual animated neon card rule"
        rule = neon.group(1)
        assert "border:2px solid transparent!important" in rule
        assert "padding-box" in rule and "border-box" in rule
        assert "backdrop-filter:none!important" in rule
        assert "-webkit-backdrop-filter:none!important" in rule
        assert "isolation:isolate" in rule
        # No second square border, inset overlay, or clipping of card content.
        assert "clip-path" not in rule
        assert "overflow:hidden" not in rule
        assert "overflow:clip" not in rule


def test_scrollable_trade_cards_and_accessible_focus_survive_corner_cleanup():
    html = rendered("trade")
    assert 'body[data-page="trade"] .forecast{min-height:0;height:100%;padding:18px 22px 14px;overflow-y:auto' in html
    assert 'body[data-page="trade"] .context>.manual-trade{min-height:0;height:100%;overflow-y:auto' in html
    assert "button:focus-visible,select:focus-visible" in html
    assert "@keyframes ke-rainbow-cycle" in html
    assert 'id="manual-submit"' in html
