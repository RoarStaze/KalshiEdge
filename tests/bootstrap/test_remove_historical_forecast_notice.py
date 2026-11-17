"""Do not show an extra historical-forecast notification card on Trade."""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.round_price_ui import HERO_STYLE, integrate_round_price_hero


def test_trade_forecast_has_no_historical_record_notice():
    page = integrate_round_price_hero(PAGE)
    assert 'class="record-note"' not in page
    assert 'id="record-title"' not in page
    assert 'id="record-description"' not in page
    assert 'text("record-title",' not in page
    assert 'text("record-description",' not in page
    assert ".forecast .record-note" not in HERO_STYLE
    # Retain the requested live indicators and the optional forecast details.
    assert 'id="confidence"' in page
    assert 'id="scalp-reversal-risk"' in page
    assert 'id="scalp-flip-alert"' in page
    assert 'id="record-details"' in page
