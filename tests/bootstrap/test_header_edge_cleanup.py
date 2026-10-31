"""Regression for double grey hairlines around the routed Trade headers."""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.site_pages import render_site_page


def rendered(section="trade"):
    base = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(base, section)


def test_trade_removes_header_and_buy_sell_separator_hairlines():
    page = rendered()
    # Decorative horizontal rules sit directly above the rounded UI elements.
    assert 'body[data-page="trade"] .site-header{border-bottom:0}' in page
    assert 'body[data-page="trade"] .manual-trade .mt-top{border-bottom:0}' in page
    # Keep rounded panel boundaries and intentionally outlined controls.
    assert 'border-radius:20px' in page
    assert '.manual-trade .mt-pills{border-color:#34413f}' in page
    assert 'button:focus-visible,select:focus-visible' in page


def test_all_pages_keep_their_nav_and_buy_sell_features():
    for section in ("trade", "train", "history", "api"):
        page = rendered(section)
        assert 'aria-label="Primary navigation"' in page
        assert 'href="/trade"' in page
        assert 'href="/api"' in page
        assert 'id="manual-trade"' in page
    trade = rendered()
    assert 'id="manual-submit"' in trade
    assert 'id="round-price-hero"' in trade
