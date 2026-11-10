"""The cinematic background stays quiet; only decorative panel outlines orbit."""
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


def test_no_noisy_particle_scene_on_any_page():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        assert 'id="ke-live-canvas"' not in html
        assert 'class="ke-live-scene"' not in html
        assert "ke-live-grid" not in html
        assert "ke-live-beam" not in html
        assert "ke-particles" not in html
        assert "requestAnimationFrame" not in html
        assert 'url("/assets/kalshiedge-neon-city.webp")' in html
        assert "filter:blur(" in html
        assert "prefers-reduced-motion" in html


def test_rainbow_runner_traces_real_card_edges_without_heading_overlay():
    html = render("trade")
    assert "ke-neon-runner" in html
    assert "@keyframes ke-rainbow-orbit" in html
    assert 'setAttribute("viewBox"' in html
    assert 'setAttribute("pathLength","100")' in html
    assert "stroke-dashoffset:-100" in html
    assert "ke-rainbow-gradient" in html
    assert ".panel-label" not in html.split("const boxSelector=")[1].split(".join")[0]
    for element in (".site-nav", ".forecast", ".context>.manual-trade",
                    ".training-card", ".api-card", ".hero-price-surface",
                    ".hero-clock-panel", ".mt-inputbox", ".mt-pills",
                    ".training-step", ".history-view .card", ".kpi"):
        assert element in html


def test_order_training_and_history_dom_ids_survive_all_routes():
    for route in ("trade", "train", "history", "api"):
        html = render(route)
        for field in ("manual-submit", "train-now", "history-rows", "api-credentials-form"):
            assert html.count('id="' + field + '"') == 1
        assert html.count('class="site-nav-link') == 4
        assert "pointer-events:none" in html
