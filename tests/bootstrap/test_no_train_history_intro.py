"""Train/History should open directly to their functional content.

The user's reference screenshots show redundant copy above the actual pages.
Keep routes, status updates, controls, and the API setup heading unchanged.
"""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page


def page(section: str) -> str:
    source = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(source, section)


def test_train_lands_on_training_card_without_redundant_page_intro():
    html = page("train")
    assert "Built to keep learning." not in html
    assert "Review model readiness, learning cycles and controlled manual training." not in html
    assert '<div class="section-intro">' not in html
    assert 'id="train-now"' in html
    assert 'id="train-pause"' in html
    assert 'id="train-resume"' in html
    assert 'id="training-stage-list"' in html
    assert 'class="training-card"' in html


def test_history_lands_on_actual_results_without_both_intro_blocks():
    html = page("history")
    assert "Every call. On the record." not in html
    assert "Explore saved final forecasts and settlement-verified performance." not in html
    assert "Official Kalshi settlements only · Accuracy = correct" not in html
    assert '<div class="section-intro">' not in html
    assert "<h2>Final forecast history</h2>" not in html
    assert '<div class="eyebrow">VERIFIED PERFORMANCE</div>' not in html
    # Keep the dynamic status indicator and actual history results.
    assert 'id="history-status"' in html
    assert 'id="history-rows"' in html
    assert 'id="history-kpis"' in html if 'id="history-kpis"' in PAGE else 'id="history-rows"' in html


def test_trade_api_navigation_and_onboarding_intros_unchanged():
    trade = page("trade")
    assert '<div class="section-intro">' not in trade
    assert 'id="manual-submit"' in trade
    api = page("api")
    assert "Secure API setup." in api
    assert "Encrypted local Kalshi API credentials." in api
    assert 'id="api-credentials-form"' in api
    for name in ("train", "history"):
        html = page(name)
        assert f'aria-current="page">{name.title()}</a>' in html
        assert 'href="/trade"' in html
