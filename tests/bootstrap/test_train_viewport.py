"""Desktop Train remains a single-screen, readable seven-stage dashboard."""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.site_pages import render_site_page


def _page(route="train"):
    return render_site_page(
        integrate_api_credentials(integrate_manual_trade(integrate_round_price_hero(PAGE))),
        route,
    )


def test_desktop_train_fits_viewport_with_side_by_side_readiness_and_progress():
    html=_page()
    assert "@media(min-width:1101px) and (min-height:761px)" in html
    assert 'body[data-page="train"]{height:100dvh;overflow:hidden}' in html
    assert 'body[data-page="train"] .shell{display:flex;flex-direction:column;height:100dvh' in html
    assert 'body[data-page="train"] .training-card{display:grid;grid-template-columns:' in html
    assert 'body[data-page="train"] .training-head{grid-column:1/-1' in html
    assert 'body[data-page="train"] .training-forward{grid-column:1;grid-row:2' in html
    assert 'body[data-page="train"] .training-live-panel{grid-column:2;grid-row:2' in html
    assert 'body[data-page="train"] footer{display:none!important}' in html


def test_train_milestones_and_control_buttons_remain_present_and_legible():
    html=_page()
    for element in (
        "train-now","train-pause","train-resume",
        "training-stage-list","training-comparison","training-outcome",
        "dataset-build-detail","training-worker-progress","training-headline",
        "forward-480-reason","baseline-retrain-ready",
    ):
        assert f'id="{element}"' in html
    assert "grid-template-columns:repeat(7,minmax(0,1fr))" in html
    assert "overflow-wrap:anywhere" in html


def test_small_or_short_screens_fall_back_to_readable_scroll_not_hidden_controls():
    html=_page()
    assert "@media(max-width:1100px), (max-height:760px)" in html
    assert 'body[data-page="train"]{height:auto;overflow-y:auto}' in html
    assert 'body[data-page="train"] .training-card{display:block' in html
    assert "id=\"train-now\"" in _page("trade")
