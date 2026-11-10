"""Live cinematic atmosphere and perimeter-tracer regression tests."""
from __future__ import annotations

import re
import shutil
import subprocess

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

def test_decorative_script_is_valid_javascript():
    node = shutil.which("node")
    if node is None:
        return  # Some developer installations do not include Node.
    html = page("trade")
    js = next(chunk for chunk in re.findall(r"<script>(.*?)</script>", html, re.S)
              if 'const boxSelector' in chunk)
    result = subprocess.run(
        [node, "--check"], input=js, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_real_browser_initializes_overlay_and_motion(tmp_path):
    chrome = (shutil.which("chromium") or shutil.which("google-chrome")
              or shutil.which("chromium-browser"))
    if chrome is None:
        return  # Optional local smoke test; CI Python/DOM checks still run.
    html = page("trade")
    probe = """<script>
setTimeout(function(){
 const border=document.querySelector(".forecast .ke-neon-runner");
 const beam=document.querySelector(".ke-live-beam");
 const canvas=document.getElementById("ke-live-canvas");
 document.body.dataset.keVisualProbe=[
   Boolean(border),
   Boolean(border&&border.getAnimations().length),
   Boolean(beam&&beam.getAnimations().length),
   Boolean(canvas&&canvas.width>0&&canvas.height>0)
 ].join(",");
},350);
</script>"""
    html = html.replace("</body>", probe+"</body>", 1)
    path = tmp_path / "kalshiedge-browser-smoke.html"
    path.write_text(html, encoding="utf-8")
    result = subprocess.run(
        [chrome, "--headless", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", "--no-first-run", "--disable-background-networking",
         "--virtual-time-budget=1000", "--dump-dom", path.as_uri()],
        capture_output=True, text=True, timeout=25
    )
    assert result.returncode == 0, result.stderr[-1200:]
    assert 'data-ke-visual-probe="true,true,true,true"' in result.stdout
