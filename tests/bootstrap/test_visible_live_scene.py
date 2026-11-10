"""Rendered geometry regression: no stray header box and visibly orbiting rainbow."""
from __future__ import annotations
import re
import shutil
import subprocess

from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page


def page(name):
    template=integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    return render_site_page(template,name)


def test_all_routes_clean_background_with_same_rainbow_border_system():
    for route in ("trade","train","history","api"):
        html=page(route)
        assert "getContext(" not in html
        assert "ke-live-beam" not in html
        assert "ke-live-grid" not in html
        assert "ke-particle" not in html
        assert "ke-rainbow-orbit" in html
        assert "ResizeObserver" in html
        assert "MutationObserver" in html
        assert "stroke-dasharray" in html
        assert 'url("/assets/kalshiedge-neon-city.webp")' in html


def test_animation_javascript_parses():
    node=shutil.which("node")
    if not node:
        return
    html=page("trade")
    js=next(script for script in re.findall(r"<script>(.*?)</script>",html,re.S)
            if "const boxSelector=" in script)
    p=subprocess.run([node,"--check"],input=js,capture_output=True,text=True,timeout=10)
    assert p.returncode==0,p.stderr


def test_browser_rainbow_follows_card_perimeter_and_animates(tmp_path):
    chrome=(shutil.which("chromium") or shutil.which("google-chrome")
            or shutil.which("chromium-browser"))
    if not chrome:
        return  # CI without Chromium still checks HTML and JS contracts.
    html=page("trade")
    probe="""<script>
setTimeout(()=>{
 const node=document.querySelector(".forecast");
 const svg=node?.querySelector(":scope > svg.ke-border-overlay");
 const rect=svg?.querySelector("rect.ke-neon-runner");
 const view=svg?.viewBox.baseVal;
 const a=rect?parseFloat(getComputedStyle(rect).strokeDashoffset):NaN;
 const widthOk=!!view && Math.abs(view.width-node.getBoundingClientRect().width)<3;
 const heightOk=!!view && Math.abs(view.height-node.getBoundingClientRect().height)<3;
 const boxOk=!!rect && rect.getBoundingClientRect().width>node.clientWidth-8;
 const labelOk=!document.querySelector(".panel-label > .ke-border-overlay");
 const colorsOk=(svg?.querySelectorAll("linearGradient stop").length||0)>=4;
 setTimeout(()=>{
  const b=rect?parseFloat(getComputedStyle(rect).strokeDashoffset):NaN;
  document.body.dataset.rainbowProbe=[
   widthOk,heightOk,boxOk,labelOk,colorsOk,
   Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)>2
  ].join(",");
 },400);
},150);
</script>"""
    html=html.replace("</body>",probe+"</body>",1)
    file=tmp_path/"rainbow-geometry.html"
    file.write_text(html,encoding="utf-8")
    p=subprocess.run([chrome,"--headless","--no-sandbox","--disable-gpu",
        "--disable-dev-shm-usage","--no-first-run",
        "--virtual-time-budget=1600","--dump-dom",file.as_uri()],
        capture_output=True,text=True,timeout=35)
    assert p.returncode==0,p.stderr[-2000:]
    assert 'data-rainbow-probe="true,true,true,true,true,true"' in p.stdout
