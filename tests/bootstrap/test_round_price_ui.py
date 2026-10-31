"""Regression tests for the approved live BTC price hero.

Presentation only: live.py, V5/V6.1 policy and sealed forecast records must remain
unchanged. The same /api/live prediction payload supplies the BRTI reference.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.round_price_ui import HERO_STYLE, integrate_round_price_hero


def test_approved_price_and_time_hero_is_in_main_dashboard():
    page = integrate_round_price_hero(PAGE)
    assert page.count('id="round-price-hero"') == 1
    forecast=page.index('<article class="card forecast">')
    article_end=page.index("</article>",forecast)
    banner=page.index('id="lock-banner"',forecast)
    hero=page.index('id="round-price-hero"',forecast)
    direction=page.index('id="direction"',forecast)
    assert forecast < banner < hero < direction < article_end
    assert page.index('class="layout"') < forecast
    assert page.count('class="card round-price-hero"') == 0
    assert page.count('id="round-countdown"') == 0
    assert page.count('id="lock-time"') == 0
    for element in (
        'id="hero-strike"',
        'id="hero-current"',
        'id="hero-delta"',
        'id="hero-pct"',
        'id="hero-countdown"',
        'id="hero-lock-at"',
        'id="hero-price-freshness"',
    ):
        assert page.count(element) == 1, element
    assert "PRICE TO BEAT" in page
    assert "CURRENT PRICE" in page
    assert "ROUND CLOSES IN" in page
    assert "Kalshi BRTI" in page
    assert "America/New_York" in page
    assert "font-variant-numeric:tabular-nums" in page


def test_live_tick_uses_correct_benchmark_and_resets_on_stale_or_new_market():
    page = integrate_round_price_hero(PAGE)
    assert "prediction?.brti_value" in page
    assert "prediction?.brti_source_ts_ns" in page
    assert "data.strike" in page
    assert "Math.abs(delta)" in page
    assert "const delta=price-strike;" in page
    assert "Math.abs(delta)/strike*100" in page
    assert "lastMarketTicker" in page
    assert "lastObservedPrice" in page
    assert "setInterval" in page
    assert 'data.status' in page
    assert "hero-current" in page
    assert "hero-delta" in page
    assert "price-source" in page
    assert "fetch(" not in page[page.index('function renderRoundPriceHero'):page.index('function renderLive(data){')]


def test_integration_preserves_original_forecast_and_live_status_elements():
    page = integrate_round_price_hero(PAGE)
    for id_value in ("direction", "confidence", "record-details", "strike", "btc", "train-now", "history-rows"):
        assert f'id="{id_value}"' in page
    assert "THIS ROUND'S FORECAST" in page
    assert "FINAL FORECAST" in page
    assert "setInterval(refreshLive" in page

def test_forecast_retains_original_page_grid_and_card_dimensions():
    page=integrate_round_price_hero(PAGE)
    # Original 445px forecast column and outer card CSS must remain unchanged.
    assert ".layout{display:grid;grid-template-columns:minmax(325px,445px) minmax(0,1fr)" in page
    assert ".forecast{position:relative;padding:25px 26px 23px;border-top:2px solid var(--cyan);min-height:535px}" in page
    assert ".layout{" not in HERO_STYLE
    assert ".layout>" not in HERO_STYLE
    assert ".forecast{padding:" not in HERO_STYLE
    assert ".forecast{min-height:" not in HERO_STYLE
    assert ".context{" not in HERO_STYLE
    assert "container-type:inline-size" in HERO_STYLE
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in HERO_STYLE
    assert "flex-direction:column" not in page[page.index(".hero-price-grid"):page.index(".hero-price-grid")+120]
    assert ".forecast .round-price-hero" in page
    assert "function clock(){updateRoundPriceClock();" in page
    assert 'id="hero-lock-at"' in page
    assert '<span id="hero-delta">' in page


def test_mockup_css_scoped_and_other_cards_preserved():
    page=integrate_round_price_hero(PAGE)
    assert page.count('id="round-price-hero"') == 1
    assert page.index('<article class="card forecast">') < page.index('id="round-price-hero"') < page.index('</article>')
    assert 'class="context"' in page
    assert '.forecast .hero-clock-panel' in HERO_STYLE
    assert '.forecast .hero-price-number' in HERO_STYLE
    assert "@container" in HERO_STYLE
