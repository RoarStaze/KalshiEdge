"""Isolated WeatherEdge AI market pages.

Only reader of external weather SQLite aggregates; never imports the BTC
predictor, trading widgets, credentials, or WeatherEdge live execution.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.site_pages import STYLE, MARKET_SELECTOR_STYLE, render_market_selector
from kalshi_edge.bootstrap.site_motion import MOTION_STYLE, MOTION_SCRIPT


WEATHER_STYLE = r"""
body[data-market="weather"]{--mint:#85d8f5}
body[data-market="weather"] .market-picker-symbol{
 background:linear-gradient(140deg,#b1efff,#5792b8);color:#062b42}
body[data-market="weather"] .market-picker summary{border-color:rgba(121,202,245,.58)}
body[data-market="weather"] .market-picker-menu a:hover,
body[data-market="weather"] .market-picker-menu a[aria-current="true"]{
 background:rgba(41,103,143,.38);color:#c4ecff}
body[data-market="weather"] .site-nav-link.is-active{
 background:rgba(32,89,123,.42);color:#c5eaff;
 box-shadow:inset 0 0 0 1px rgba(101,194,241,.5)}
.wx-shell{display:grid;gap:19px;max-width:1450px;margin:24px auto 0}
.wx-heading{display:flex;align-items:end;justify-content:space-between;gap:18px;flex-wrap:wrap}
.wx-kicker{font-size:10px;letter-spacing:2px;color:#a0deff;font-weight:850}
.wx-heading h1{font-size:clamp(28px,3vw,42px);margin:8px 0 6px;letter-spacing:-1.2px}
.wx-heading p{font-size:13px;line-height:1.6;color:#aac0cb;margin:0;max-width:730px}
.wx-badge{padding:9px 13px;border-radius:99px;border:1px solid #93cfee73;
 background:#16374e70;color:#c9eeff;letter-spacing:1px;font-size:10px;font-weight:800}
.wx-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:17px}
.wx-panel{--ke-rainbow-panel:linear-gradient(155deg,rgba(7,26,30,.94),rgba(4,13,20,.96));
 border:2px solid transparent!important;border-radius:20px;padding:23px;
 background:var(--ke-rainbow-panel) padding-box,
 conic-gradient(from var(--ke-rainbow-angle),#31ffcc,#2edbff,#8368ff,#ff3abc,#f6fc67,#39ffb0,#31ffcc) border-box!important;
 animation:ke-rainbow-cycle 6s linear infinite;box-shadow:0 20px 55px #0009}
.wx-panel h2{font-size:18px;margin:0 0 13px}
.wx-panel p{font-size:13px;line-height:1.6;color:#b2c3cf}
.wx-notice{padding:14px 16px;border-radius:11px;border:1px solid #a4cde061;
 background:#173a4a57;color:#d1ecfb;font-size:12px;line-height:1.65}
.wx-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:17px 0}
.wx-stat{background:#0c2837b0;border:1px solid #6eafca48;border-radius:13px;padding:17px}
.wx-stat span{display:block;font-size:11px;color:#a2bac7}
.wx-stat strong{display:block;font-size:25px;color:#b5eaff;margin-top:7px}
.wx-rows{display:grid;gap:0;margin-top:12px}
.wx-row{display:flex;justify-content:space-between;gap:16px;
 border-bottom:1px solid #7391aa38;padding:13px 0;font-size:12px}
.wx-row span{color:#b7c9cf}
.wx-row strong{color:#cef0ff;text-align:right;overflow-wrap:anywhere}
.wx-empty{padding:19px;border:1px dashed #648ba38c;border-radius:12px;
 color:#adbdc8;font-size:13px;line-height:1.6}
.wx-sources{display:grid;gap:10px}
.wx-station{padding:13px;border:1px solid #628da058;background:#0d27328c;border-radius:11px}
.wx-station-title{font-weight:800;color:#d8f4ff;font-size:14px;margin-bottom:7px}
.wx-station-line{font-size:12px;color:#b2d6e1;line-height:1.7}
.wx-footer{border-top:1px solid #ffffff1a;padding:16px 0;color:#90abb9;font-size:11px}
.wx-panel code{color:#c0e7f9;overflow-wrap:anywhere}
@media(max-width:920px){.wx-grid{grid-template-columns:1fr}.wx-panel{padding:19px}}
@media(max-width:700px){.wx-shell{margin-top:16px}.wx-heading h1{font-size:28px}.wx-panel{padding:15px}}
@media(prefers-reduced-motion:reduce){.wx-panel{animation:none}}
"""

WEATHER_NAV = (
    ("forecast", "Forecast", "/weather"),
    ("train", "Train", "/weather/train"),
    ("history", "History", "/weather/history"),
    ("data", "Data", "/weather/data"),
)
WEATHER_SECTIONS = {
    "forecast": ("Weather forecast", "Daily high-temperature research for selected U.S. airport stations."),
    "train": ("Weather model laboratory", "Station-specific probabilistic models must be trained and independently validated."),
    "history": ("Weather history", "Only independently resolved weather forecasts and paper trades may count toward performance."),
    "data": ("Weather data readiness", "Inspect real HRRR, NBM and METAR collector receipts without treating them as settlement truth."),
}

_FORECAST = """
<div class="wx-grid">
 <article class="wx-panel"><h2>Daily high-temperature probabilities</h2>
  <div class="wx-notice">No calibrated station-level temperature distribution is active in KalshiEdge.
  WeatherEdge AI remains a separate research/paper-trading engine. Do not interpret
  incoming model forecasts as settlement-certified Kalshi probabilities.</div>
  <div class="wx-stats">
   <div class="wx-stat"><span>Forecast availability</span><strong>UNVALIDATED</strong></div>
   <div class="wx-stat"><span>Order submission</span><strong>DISABLED</strong></div>
  </div>
  <p>HRRR/NBM and station observations become visible here as the external
  collector writes to the shared weather database. The UI never submits real orders.</p>
 </article>
 <article class="wx-panel"><h2>Live data readiness</h2>
  <div class="wx-rows">
   <div class="wx-row"><span>Collector database</span><strong id="wx-db">CHECKING</strong></div>
   <div class="wx-row"><span>Weather records</span><strong id="wx-samples">—</strong></div>
   <div class="wx-row"><span>Stations</span><strong id="wx-station-count">—</strong></div>
   <div class="wx-row"><span>Settlement labels</span><strong>NOT CERTIFIED</strong></div>
  </div><p id="wx-updated">Waiting for collector status…</p>
 </article>
</div>
"""

_TRAIN = """
<div class="wx-grid"><article class="wx-panel"><h2>Station model training</h2>
 <div class="wx-notice">Training is not launched from this interface.
 Use the separately installed WeatherEdge AI command-line research workflow
 after collecting verified point-in-time features and independently verified
 contract-specific settlement labels.</div>
 <div class="wx-rows">
  <div class="wx-row"><span>Training from KalshiEdge UI</span><strong>DISABLED</strong></div>
  <div class="wx-row"><span>Calibration certification</span><strong>NOT VERIFIED</strong></div>
  <div class="wx-row"><span>OOS profitability</span><strong>NOT VERIFIED</strong></div>
  <div class="wx-row"><span>Live order permission</span><strong>DISABLED</strong></div>
 </div></article>
 <article class="wx-panel"><h2>Collection prerequisites</h2><div class="wx-sources" id="wx-stations">
 <div class="wx-empty">Checking available airport station data…</div></div></article></div>
"""

_HISTORY = """
<div class="wx-grid"><article class="wx-panel"><h2>Forecast and trading history</h2>
 <div class="wx-empty"><strong>No independently validated weather performance is displayed.</strong>
 WeatherEdge's research audit records and simulated fills must be independently
 evaluated before being treated as backtest evidence or paper P&amp;L.</div>
 <div class="wx-rows"><div class="wx-row"><span>Exchange-confirmed P&amp;L</span><strong>NONE</strong></div>
 <div class="wx-row"><span>30-day paper validation</span><strong>NOT CERTIFIED</strong></div>
 <div class="wx-row"><span>Market-specific settlement source</span><strong>REVIEW REQUIRED</strong></div>
 </div></article><article class="wx-panel"><h2>Current stored inputs</h2>
 <p id="wx-updated">Checking collection status…</p>
 <div class="wx-sources" id="wx-stations"><div class="wx-empty">No data loaded.</div></div></article></div>
"""

_DATA = """
<div class="wx-grid"><article class="wx-panel"><h2>Station data by source</h2>
 <div class="wx-notice">These counts are actual records from the external WeatherEdge
 SQLite collector, not forecast accuracy, market fills or official settlement outcomes.</div>
 <div class="wx-stats"><div class="wx-stat"><span>Records</span><strong id="wx-samples">—</strong></div>
 <div class="wx-stat"><span>Stations</span><strong id="wx-station-count">—</strong></div></div>
 <div class="wx-sources" id="wx-stations"><div class="wx-empty">Checking collector database…</div></div>
 </article><article class="wx-panel"><h2>Collector integration</h2>
 <div class="wx-rows">
 <div class="wx-row"><span>Database</span><strong id="wx-db">CHECKING</strong></div>
 <div class="wx-row"><span>Kalshi settlement audit</span><strong>REVIEW REQUIRED</strong></div>
 <div class="wx-row"><span>Live order submission</span><strong>DISABLED</strong></div>
 </div><p>Run the separately installed WeatherEdge AI collector from the
 KalshiEdge project root with <code>--db data/weather/weatheredge.sqlite3</code>
 to populate this screen.</p><p id="wx-updated">Waiting for collector status…</p>
 </article></div>
"""

WEATHER_CONTENT = {"forecast": _FORECAST, "train": _TRAIN, "history": _HISTORY, "data": _DATA}

WEATHER_STATUS_SCRIPT = r"""<script>
(function(){
 "use strict";
 const node=id=>document.getElementById(id);
 function put(id,value){const el=node(id);if(el)el.textContent=String(value);}
 function stamp(iso){
   if(!iso)return "No records received";
   const date=new Date(iso);
   return Number.isNaN(date.getTime())?"Receipt timestamp unavailable":"Latest receipt: "+date.toLocaleString();
 }
 function showStations(stations){
   const el=node("wx-stations");if(!el)return;
   el.replaceChildren();
   if(!stations.length){
     const msg=document.createElement("div");
     msg.className="wx-empty";msg.textContent="No HRRR, NBM or METAR records available yet.";
     el.appendChild(msg);return;
   }
   for(const station of stations){
     const card=document.createElement("div");card.className="wx-station";
     const title=document.createElement("div");title.className="wx-station-title";
     title.textContent=station.station;card.appendChild(title);
     for(const source of ["HRRR","NBM","METAR"]){
       const row=document.createElement("div");row.className="wx-station-line";
       const info=station.sources?.[source];
       row.textContent=source+": "+(info?info.count+" records · "+stamp(info.latest_received_at):"no data");
       card.appendChild(row);
     }
     el.appendChild(card);
   }
 }
 async function refresh(){
   try{
     const response=await fetch("/api/weather/status",{cache:"no-store"});
     if(!response.ok)throw new Error("Collector status request failed");
     const state=await response.json();
     put("wx-db",state.storage_state||"UNKNOWN");
     put("wx-samples",state.sample_count??0);
     put("wx-station-count",state.station_count??0);
     put("wx-updated",stamp(state.last_received_at));
     showStations(Array.isArray(state.stations)?state.stations:[]);
   }catch(_error){
     put("wx-db","UNAVAILABLE");put("wx-updated","Collector status unavailable");
     showStations([]);
   }
 }
 refresh();setInterval(refresh,15000);
})();
</script>"""


def render_weather_page(section: str) -> str:
    """Render isolated read-only WeatherEdge workspace, no Bitcoin page scripts."""
    if section not in WEATHER_SECTIONS:
        raise ValueError("Unknown WeatherEdge section")
    base_css = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
    css = "\n".join((base_css, STYLE, MOTION_STYLE, MARKET_SELECTOR_STYLE, WEATHER_STYLE))
    links = "".join(
        f'<a class="site-nav-link{" is-active" if key == section else ""}" '
        f'href="{url}"{" aria-current=\"page\"" if key == section else ""}>{label}</a>'
        for key, label, url in WEATHER_NAV
    )
    title, description = WEATHER_SECTIONS[section]
    selector = render_market_selector("weather")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08161b">
<title>KalshiEdge · Weather · {title}</title>
<style>{css}</style></head>
<body data-page="weather" data-market="weather">
<div class="shell"><header class="site-header">
{selector}<nav class="site-nav" aria-label="Weather navigation">{links}</nav>
<div class="header-right"><span class="chip">PAPER ONLY</span></div>
</header>
<main class="wx-shell" data-weather-section="{section}">
<section class="wx-heading" aria-label="WeatherEdge AI research">
<div><div class="wx-kicker">KALSHIEDGE / WEATHER · EXPERIMENTAL</div>
<h1>WeatherEdge AI · {title}</h1><p>{description}</p></div>
<span class="wx-badge">NO LIVE ORDERS</span></section>
{WEATHER_CONTENT[section]}
</main>
<footer class="wx-footer">WeatherEdge AI · station research and paper-only data.
Weather observations are not necessarily Kalshi's official settlement measurements.</footer>
</div>{WEATHER_STATUS_SCRIPT}{MOTION_SCRIPT}</body></html>"""
