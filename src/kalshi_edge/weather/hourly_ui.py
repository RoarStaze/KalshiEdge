"""Hourly-only WeatherEdge user interface, with no daily-high forecasts."""
from __future__ import annotations

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.site_pages import (
    STYLE, MARKET_SELECTOR_STYLE, render_market_selector,
)
from kalshi_edge.bootstrap.site_motion import MOTION_STYLE, MOTION_SCRIPT

PAGES = {
    "forecast": ("Hourly temperature", "Predict the next Kalshi Weather Index observation and every listed hourly YES threshold."),
    "train": ("Hourly calibration", "Measure the proper scores of frozen, pre-settlement hourly predictions, not daily airport highs."),
    "history": ("Hourly history", "Only one precommitted threshold per hour enters performance scoring; no fabricated results."),
    "data": ("Hourly index feeds", "Inspect official Kalshi index values and hourly collector health."),
}
NAV = (
    ("forecast","Forecast","/weather"),
    ("train","Calibration","/weather/train"),
    ("history","History","/weather/history"),
    ("data","Feeds","/weather/data"),
)
MAIN = """
<div class="wx-grid">
 <article class="wx-panel">
  <div class="wx-prediction-heading"><h2>Next-hour Kalshi Weather Index</h2>
   <label class="wx-picker-label" for="wx-hourly-city">City
    <select id="wx-hourly-city" aria-label="Hourly index city">
     <option value="chicago">Chicago Metro · KXTEMPCHIHS</option>
     <option value="nyc">New York City · KXTEMPNYCHS</option>
     <option value="miami">Miami · KXTEMPMIAH</option>
     <option value="la-coastal">Coastal Los Angeles · KXTEMPLAXHS</option>
    </select></label></div>
  <div class="wx-notice">HOURLY ONLY · The settlement underlying is
   Kalshi's minute-resolution multi-station Weather Index, not any airport's
   daily high or a METAR reading. Probabilities are experimental Gaussian
   extrapolations, NOT demonstrated calibration or tradeable expected value.
   WeatherEdge never submits orders.</div>
  <div class="wx-stats">
   <div class="wx-stat"><span>Latest official index</span>
    <strong id="wx-hourly-current">—</strong></div>
   <div class="wx-stat"><span>Predicted index at target hour</span>
    <strong id="wx-hourly-mean">—</strong></div>
   <div class="wx-stat"><span>90% modeled interval (not validated)</span>
    <strong id="wx-hourly-interval">—</strong></div>
   <div class="wx-stat"><span>Minutes to target</span>
    <strong id="wx-hourly-minutes">—</strong></div>
  </div>
  <div class="wx-rows">
   <div class="wx-row"><span>Prediction readiness</span><strong id="wx-hourly-state">WAITING</strong></div>
   <div class="wx-row"><span>Target hour (Eastern Time)</span><strong id="wx-hourly-et">—</strong></div>
   <div class="wx-row"><span>Target UTC</span><strong id="wx-hourly-utc">—</strong></div>
   <div class="wx-row"><span>Hourly Kalshi event</span><strong id="wx-hourly-event">—</strong></div>
   <div class="wx-row"><span>Last canonical minute (UTC)</span><strong id="wx-hourly-event-time">—</strong></div>
   <div class="wx-row"><span>Locally received (UTC)</span><strong id="wx-hourly-receipt">—</strong></div>
   <div class="wx-row"><span>Official data publisher</span><strong>Kalshi Weather Index</strong></div>
  </div>
  <p id="wx-hourly-diagnostic" role="status">Waiting for the official hourly index and open contract ladder.</p>
  <div id="wx-hourly-health" class="wx-notice" role="status">Automatic hourly collectors initializing…</div>
 </article>
 <article class="wx-panel">
  <h2>Hourly threshold probabilities · YES</h2>
  <p>Each threshold is a separate directional contract, for example
   “55°F or above.” The plotted percentage is a model estimate, not a
   price or an EV recommendation.</p>
  <div id="wx-hourly-ladder" class="wx-buckets" aria-live="polite">
   <div class="wx-empty">No eligible current-hour contracts or index data yet.</div>
  </div>
  <div class="wx-rows">
   <div class="wx-row"><span>Temperature precision</span><strong>0.01°F index</strong></div>
   <div class="wx-row"><span>Probability calibration</span><strong>NOT CERTIFIED</strong></div>
   <div class="wx-row"><span>Real weather order submission</span><strong>DISABLED</strong></div>
  </div>
 </article>
</div>
"""
CALIBRATION = """
<article class="wx-panel">
 <h2>Hourly calibration · prospective evidence</h2>
 <div class="wx-notice">One temperature threshold is chosen before each event,
  with its prediction frozen for later comparison. The official index and
  Kalshi's final binary market result must agree. Scores are withheld until
  at least 10 genuinely matched hourly events have settled. This does not
  certify calibration or profitability.</div>
 <div class="wx-stats">
  <div class="wx-stat"><span>Matched hourly events</span><strong id="wx-hm-matched">0</strong></div>
  <div class="wx-stat"><span>Binary Brier score</span><strong id="wx-hm-brier">NOT MEASURED</strong></div>
  <div class="wx-stat"><span>Binary log loss</span><strong id="wx-hm-logloss">—</strong></div>
  <div class="wx-stat"><span>Observed 90% modeled interval coverage</span>
   <strong id="wx-hm-coverage">NOT MEASURED</strong></div>
 </div>
 <div class="wx-rows">
  <div class="wx-row"><span>Evidence status</span><strong id="wx-hm-state">WAITING</strong></div>
  <div class="wx-row"><span>Independently certified</span><strong>NO</strong></div>
  <div class="wx-row"><span>Live trading enabled</span><strong>NO</strong></div>
 </div>
</article>
"""
DATA = """
<article class="wx-panel">
 <h2>Hourly index source health</h2>
 <div class="wx-stats">
  <div class="wx-stat"><span>Official index minutes stored</span>
   <strong id="wx-hd-points">0</strong></div>
  <div class="wx-stat"><span>Cities with recorded index data</span>
   <strong id="wx-hd-cities">0</strong></div>
 </div>
 <div class="wx-rows">
  <div class="wx-row"><span>Local hourly database</span><strong id="wx-hd-state">WAITING</strong></div>
  <div class="wx-row"><span>Latest source receipt</span><strong id="wx-hd-receipt">—</strong></div>
  <div class="wx-row"><span>Settlement worker</span><strong id="wx-hd-settlement">WAITING</strong></div>
 </div>
 <p>All indexed minute readings use UTC event timestamps and separate local
 receipt timestamps. Daily-high snapshots remain archived but are excluded
 from the hourly market forecast and evaluation.</p>
</article>
"""
SCRIPT = r"""<script>
(() => {
 "use strict";
 const el = id=>document.getElementById(id);
 const put=(id,value)=>{const node=el(id);if(node)node.textContent=String(value);};
 const f=value=>Number.isFinite(value)?value.toFixed(2)+" °F":"—";
 const t=value=>{if(!value)return "—";const d=new Date(value);
    return Number.isNaN(d.valueOf())?"—":d.toLocaleString("en-US",
      {timeZone:"America/New_York",year:"numeric",month:"short",day:"numeric",
       hour:"numeric",minute:"2-digit",timeZoneName:"short"});};
 const iso=value=>value||"—";
 const card=el("wx-hourly-city");
 let forecasts={};
 function drawLadder(rows){
    const target=el("wx-hourly-ladder");if(!target)return;
    target.replaceChildren();
    if(!Array.isArray(rows)||rows.length===0){
       const div=document.createElement("div");div.className="wx-empty";
       div.textContent="Hourly market ladder or fresh index unavailable. Automatic collection retries.";
       target.append(div);return;
    }
    for(const x of rows){
       const p=Number(x.probability_yes);
       if(!Number.isFinite(p)||p<0||p>1)continue;
       const div=document.createElement("div");div.className="wx-bucket";
       const title=document.createElement("span");title.className="wx-bucket-label";
       title.textContent="≥ "+x.threshold_f+" °F";
       const track=document.createElement("div");track.className="wx-bucket-track";
       const bar=document.createElement("div");bar.className="wx-bucket-fill";
       bar.style.width=(p*100).toFixed(3)+"%";
       track.append(bar);
       const pct=document.createElement("span");pct.className="wx-bucket-prob";
       pct.textContent=(p*100).toFixed(1)+"%";
       div.append(title,track,pct);target.append(div);
    }
 }
 function draw(){
    if(!card)return;
    const row=forecasts[card.value]||{};
    put("wx-hourly-state",row.status||"WAITING");
    put("wx-hourly-current",f(row.current_index_f));
    put("wx-hourly-mean",f(row.predicted_index_f));
    put("wx-hourly-interval",Array.isArray(row.interval_90_f)?
        row.interval_90_f.map(f).join(" – "):"NOT MEASURED");
    put("wx-hourly-minutes",Number.isFinite(row.minutes_to_target)?
        row.minutes_to_target.toFixed(0):"—");
    put("wx-hourly-utc",iso(row.target_utc));
    put("wx-hourly-et",t(row.target_utc));
    put("wx-hourly-event",iso(row.event_ticker));
    put("wx-hourly-event-time",Number.isFinite(row.source_event_ms)?
        new Date(row.source_event_ms).toISOString():"—");
    put("wx-hourly-receipt",iso(row.source_received_at));
    put("wx-hourly-diagnostic",row.status==="READY_RESEARCH"?
       "Published Kalshi index is available. The index extrapolation is uncalibrated and paper-only.":
       row.status==="NO_VERIFIED_HOURLY_MARKETS"?
       "Index is available; no verified open hourly Kalshi Weather Index threshold ladder for this target. No odds are fabricated.":
       row.status==="STALE_INDEX"?"Official index data are stale; automatic collection will retry.":
       "Waiting for fresh official index readings and matching hourly contracts. No daily-high substitute is used.");
    drawLadder(row.markets);
 }
 async function update(){
   try{
      const res=await fetch("/api/weather/hourly/forecast",{cache:"no-store"});
      if(!res.ok)throw new Error("Hourly forecast API unavailable");
      const data=await res.json();
      forecasts={};
      for(const row of data.forecasts||[])forecasts[row.city]=row;
      draw();
   }catch(err){forecasts={};draw();put("wx-hourly-state","FEED UNAVAILABLE");}
   try{
      const [status,calibration]=await Promise.all([
        fetch("/api/weather/hourly/status",{cache:"no-store"}),
        fetch("/api/weather/hourly/calibration",{cache:"no-store"})]);
      if(!status.ok||!calibration.ok)throw new Error("Hourly evidence APIs unavailable");
      const s=await status.json(), c=await calibration.json();
      put("wx-hd-points",s.sample_count??0);
      put("wx-hd-cities",s.city_count??0);
      put("wx-hd-state",s.status||"UNAVAILABLE");
      put("wx-hd-receipt",s.last_received_at||"—");
      put("wx-hm-matched",c.matched_hours??0);
      put("wx-hm-brier",Number.isFinite(c.brier)?c.brier.toFixed(4):"NOT MEASURED");
      put("wx-hm-logloss",Number.isFinite(c.log_loss)?c.log_loss.toFixed(4):"—");
      put("wx-hm-coverage",Number.isFinite(c.empirical_90_coverage)?
          (100*c.empirical_90_coverage).toFixed(1)+"%":"NOT MEASURED");
      put("wx-hm-state",c.status||"UNAVAILABLE");
      const h=s.collector||{},feed=h.hourly||{},outcome=h.hourly_settlement||{};
      put("wx-hd-settlement",outcome.state||"NOT STARTED");
      const summary=["Automatic Kalshi hourly collector: "+(h.state||"STARTING")];
      for(const city of ["chicago","nyc","miami","la-coastal"]){
        const x=feed[city]||{};
        summary.push(city+": "+(x.state||"WAITING")+
          (x.reason?" ("+x.reason+")":""));
      }
      summary.push("Settlement: "+(outcome.state||"WAITING")+
         (outcome.verified!=null?" · newly matched "+outcome.verified:""));
      put("wx-hourly-health",summary.join(" · "));
   }catch(err){
      put("wx-hourly-health","Hourly index health endpoint unavailable.");
      put("wx-hm-state","EVIDENCE API UNAVAILABLE");
   }
 }
 if(card)card.addEventListener("change",draw);
 update();setInterval(update,15000);
})();
</script>"""


def render_hourly_page(section: str, weather_style: str) -> str:
    if section not in PAGES:
        raise ValueError("Unknown WeatherEdge hourly page")
    base_css = PAGE.split("<style>",1)[1].split("</style>",1)[0]
    css = "\n".join((base_css,STYLE,MOTION_STYLE,MARKET_SELECTOR_STYLE,weather_style))
    links=[]
    for key,label,url in NAV:
        selected = " is-active" if key==section else ""
        current = ' aria-current="page"' if key==section else ""
        links.append(f'<a class="site-nav-link{selected}" href="{url}"{current}>{label}</a>')
    title,sub=PAGES[section]
    selector=render_market_selector("weather")
    details = MAIN + CALIBRATION if section=="forecast" else (
        CALIBRATION if section in ("train","history") else DATA)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08161b"><title>KalshiEdge · {title}</title>
<style>{css}</style></head><body data-page="weather" data-market="weather">
<div class="shell"><header class="site-header">
{selector}<nav class="site-nav" aria-label="Weather navigation">{''.join(links)}</nav>
<div class="header-right"><span class="chip">PAPER ONLY</span></div></header>
<main class="wx-shell" data-weather-section="{section}">
<section class="wx-heading"><div>
<div class="wx-kicker">KALSHIEDGE / WEATHER · HOURLY INDEX</div>
<h1>WeatherEdge AI · {title}</h1><p>{sub}</p></div>
<span class="wx-badge">NO LIVE ORDERS</span></section>
{details}</main>
<footer class="wx-footer">Hourly Kalshi Weather Index research · paper only.
Canonical published minute-index values are the contract underlyings.
All probabilities are unvalidated research estimates until forward evidence supports them.</footer>
</div>{SCRIPT}{MOTION_SCRIPT}</body></html>"""
