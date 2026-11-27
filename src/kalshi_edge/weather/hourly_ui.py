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
    ("forecast","Trade","/weather"),
    ("train","Train","/weather/train"),
    ("history","History","/weather/history"),
    ("data","Feeds","/weather/data"),
)
HOURLY_STYLE = """
.wx-hourly-overview{grid-template-columns:repeat(2,minmax(0,1fr))}
.wx-hourly-overview .wx-stat{min-width:0;display:flex;flex-direction:column;gap:4px}
.wx-hourly-overview .wx-stat strong{margin-top:4px;line-height:1.2;overflow-wrap:anywhere}
.wx-hourly-overview .wx-stat small{color:#9fbcc9;font-size:11px;line-height:1.5;font-weight:500}
.wx-hourly-primary strong{font-size:clamp(32px,4vw,45px)!important;color:#e6faff!important}
.wx-hourly-clock strong{font-size:clamp(32px,4vw,45px)!important;font-variant-numeric:tabular-nums;color:#e6faff!important}
.wx-hourly-contract strong{font-size:22px!important}
.wx-hourly-contract b{font-size:16px;color:#b5eaff}
.wx-hourly-caution{padding:10px 13px;background:#0c2837a0;border-left:3px solid #77b3c8;border-radius:7px}
.wx-hourly-details{padding:10px 0;color:#bddce9;font-size:12px}
.wx-hourly-details summary{cursor:pointer;color:#cce5ef;font-weight:650}
.wx-hourly-ladder .wx-bucket{display:grid;grid-template-columns:minmax(115px,1fr) auto auto;gap:10px;padding:11px 10px;border:1px solid #567c8d6b;border-radius:10px;min-width:0}
.wx-hourly-ladder .wx-bucket.is-focus{border-color:#81d5f2;background:#173e5080}
.wx-hourly-ladder .wx-bucket-label{font-size:12px}
.wx-hourly-yes,.wx-hourly-no{font-size:12px;font-weight:750;font-variant-numeric:tabular-nums}
.wx-hourly-yes{color:#a8eada}.wx-hourly-no{color:#efc3c3}
.wx-forward-cohorts{display:grid;gap:7px;margin-top:10px}
.wx-forward-cohort{border:1px solid #567c8d6b;border-radius:9px;
  padding:9px 12px;font-size:12px;line-height:1.65;overflow-wrap:anywhere}
.wx-forward-cohort strong{color:#d4ecf5}
@media(max-width:650px){.wx-hourly-overview{grid-template-columns:1fr}.wx-hourly-ladder .wx-bucket{gap:6px}}
/* Same two equal-height floating glass panels as Bitcoin's Trade view. */
body[data-weather-section="forecast"]{min-height:100vh}
.wx-shell[data-weather-section="forecast"]{margin:12px auto 0;max-width:none}
.wx-shell[data-weather-section="forecast"]>.wx-heading{display:none}
.wx-trade-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);
  gap:14px;align-items:stretch;min-height:calc(100vh - 114px)}
.wx-trade-grid>.wx-panel{min-width:0;align-self:stretch;margin:0;
  padding:18px 19px 20px;box-shadow:0 23px 52px #0009}
.wx-trade-forecast{display:flex;flex-direction:column;gap:12px}
.wx-trade-head{display:flex;justify-content:space-between;align-items:center;gap:10px}
.wx-trade-eyebrow{color:#a3eade;font-weight:850;font-size:10px;letter-spacing:2px}
.wx-trade-city-label{display:flex;align-items:center;gap:7px;font-size:11px;color:#b5cfd5}
.wx-trade-city-label select{min-width:130px;max-width:195px;padding:7px 9px;
  border:1px solid #3b777b;border-radius:9px;background:#08252c;color:#dcfff6;cursor:pointer}
.wx-trade-position{display:flex;flex-direction:column;gap:6px;
  min-height:53px;border-radius:10px;border:1px solid #65818a88;
  border-left:3px solid #aacbd0;padding:11px 13px;background:#29313870}
.wx-trade-position strong{font-size:12px;letter-spacing:.35px;color:#f5c57e}
.wx-trade-position span{font-size:11px;color:#b5cdd1}
.wx-trade-prices{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);
  border:1px solid #547b8c77;border-radius:11px;background:#071c2380;padding:15px 12px}
.wx-trade-price{min-width:0;padding:1px 13px}
.wx-trade-price-current{border-left:1px solid #52869765}
.wx-trade-stat-label{font-size:12px;font-weight:800;color:#9dc3d2;letter-spacing:1px}
.wx-trade-price strong{display:block;font-size:clamp(25px,2.5vw,35px);
  font-weight:850;letter-spacing:-.8px;line-height:1.2;margin:7px 0 5px;
  color:#f0f8f8;font-variant-numeric:tabular-nums}
.wx-trade-price-current strong{color:#25eab9}
.wx-trade-price small{display:block;font-size:10px;color:#acc4cf;line-height:1.45}
#wx-hourly-delta{display:block;min-height:17px;color:#f4b1c6;font-size:12px}
.wx-trade-timing{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);
  background:#232626bd;border:1px solid #b2803d9e;border-radius:10px;
  padding:12px 16px;gap:12px}
.wx-trade-time-left{display:flex;align-items:center;gap:12px}
.wx-trade-time-icon{font-size:30px;color:#ff913a}
.wx-trade-time-right{border-left:1px solid #78848275;padding-left:15px;display:flex;flex-direction:column}
.wx-trade-time-caption{display:block;font-size:11px;color:#c6d7e5;font-weight:850;letter-spacing:.7px}
#wx-hourly-close-clock{display:block;color:#fc973e;font-size:clamp(23px,2.7vw,30px);
  font-weight:850;font-variant-numeric:tabular-nums;line-height:1.3}
#wx-hourly-last-forecast{display:block;margin-top:8px;color:#ecf4f1;
  font-size:14px;font-weight:550;font-variant-numeric:tabular-nums}
.wx-trade-hero{margin-top:1px}
.wx-trade-direction{font-size:clamp(46px,5vw,62px);font-weight:250;line-height:1.05;
  letter-spacing:-2px;color:#ff839b;overflow-wrap:anywhere}
.wx-trade-direction.yes{color:#56ebc6}
.wx-trade-direction.no{color:#ff859b}
.wx-trade-direction.waiting{color:#b5bec9;font-size:clamp(30px,3.3vw,42px)}
.wx-trade-direction-note{font-size:11px!important;line-height:1.5!important;color:#9fb8bf!important;margin:6px 0 15px}
.wx-trade-confidence{display:flex;align-items:center;gap:12px}
#wx-hourly-confidence{font-size:30px;font-weight:350;letter-spacing:-1px;
  color:#f0f7f6;font-variant-numeric:tabular-nums}
.wx-trade-confidence b{display:block;font-size:12px;color:#eef8f5}
.wx-trade-confidence small{display:block;font-size:10px;color:#9db6bf;margin-top:3px}
.wx-trade-progress{height:4px;background:#38505b;border-radius:8px;overflow:hidden;margin:10px 0 8px}
#wx-hourly-progress{height:100%;width:0%;background:linear-gradient(90deg,#38ffd1,#54cfb8);
  transition:width .3s ease}
.wx-trade-prob-explainer{font-size:10px!important;line-height:1.55!important;margin:0!important;color:#b6cbd2!important}
.wx-trade-risk{border-top:1px solid #5b80824a;padding-top:14px;margin-top:1px}
.wx-trade-risk>div,.wx-trade-flip>div{display:flex;align-items:center;justify-content:space-between;gap:10px}
.wx-trade-risk strong,.wx-trade-flip strong{font-size:11px;letter-spacing:1px;color:#b7e7db}
#wx-hourly-risk-badge{color:#ffd68e;background:#5f431b66;border:1px solid #a87b36;
  padding:5px 9px;border-radius:7px;font-size:10px;font-weight:800;white-space:nowrap}
.wx-trade-risk p,.wx-trade-flip p{margin:6px 0 0!important;font-size:10px!important;color:#aac4c9!important}
.wx-trade-flip{background:#0b2929a8;border:1px solid #5a858080;border-radius:9px;padding:12px}
#wx-hourly-flips{color:#9feacb;font-size:10px;font-weight:800;text-align:right}
.wx-trade-diagnostic{font-size:11px!important;color:#a9c9ce!important;margin:0!important}
.wx-trade-forecast>.wx-hourly-details{margin-top:0}
.wx-trade-quote{display:flex;flex-direction:column;gap:14px}
.wx-paper-top{display:flex;align-items:center;justify-content:space-between;gap:9px}
.wx-paper-tabs{display:flex;gap:16px;font-size:14px;letter-spacing:1px}
.wx-paper-tabs strong{color:#eef9fa}.wx-paper-tabs span{color:#82909d;font-weight:800}
.wx-paper-badge{font-size:10px;color:#a5eacb;border:1px solid #45ab928f;
  border-radius:20px;padding:5px 9px;font-weight:800}
.wx-paper-market-title{display:flex;align-items:center;gap:10px;margin-top:3px}
.wx-paper-weather-symbol{display:grid;place-items:center;background:#fa9e42;color:#fff;
  width:37px;height:37px;border-radius:9px;font-size:25px;flex-shrink:0}
.wx-paper-market-title h2{font-size:clamp(18px,2.1vw,23px);margin:0;line-height:1.25;letter-spacing:-.4px}
.wx-paper-market-title small{display:block;color:#a4c6d1;font-size:11px;margin-top:5px}
.wx-paper-side-pills{display:flex;align-items:stretch;width:max-content;max-width:100%;
  border:1px solid #5c767780;background:#33363b6b;border-radius:24px;overflow:hidden}
.wx-paper-side-pills button,.wx-paper-auto{border:0;background:transparent;color:#e0e3e7;
  font-size:12px;font-weight:850;padding:10px 17px;letter-spacing:.2px;white-space:nowrap}
.wx-paper-side-pills button{cursor:pointer}
.wx-paper-side-pills button[aria-pressed="true"]{background:#ff4567;color:#12141b;border-radius:25px}
.wx-paper-side-pills button:focus-visible{outline:2px solid #8effd2;outline-offset:-3px}
.wx-paper-auto{color:#9aadb3;cursor:default}
.wx-paper-amount-label{font-size:11px;color:#b4cbd1;margin-bottom:-5px}
.wx-paper-amount-wrap{position:relative;display:flex;align-items:center;
  border:1px solid #546f767e;border-radius:9px;background:#111e20b0}
.wx-paper-amount-wrap input{width:100%;padding:15px 42px 15px 17px;border:0;
  background:transparent;color:#eefcf8;outline:0;font:inherit;font-size:14px}
.wx-paper-amount-wrap:focus-within{outline:2px solid #58b9ac}
.wx-paper-amount-wrap>span{position:absolute;right:15px;color:#c3cdd2;font-size:16px}
.wx-paper-account{font-size:11px!important;margin:-5px 0 0!important;color:#91b0b9!important}
.wx-paper-metrics{display:grid;gap:11px;margin-top:2px}
.wx-paper-metrics>div{display:flex;justify-content:space-between;gap:14px;
  align-items:baseline;min-width:0;font-size:12px}
.wx-paper-metrics span{color:#bfced5;flex:0 0 38%}
.wx-paper-metrics strong{color:#cfe6ed;font-size:11px;text-align:right;
  font-weight:600;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
#wx-paper-order-disabled{border:0;border-radius:30px;
  background:linear-gradient(95deg,#177e73,#298b77);color:#d0f6ef;
  font-weight:850;font-size:13px;padding:15px 20px;opacity:.73;cursor:not-allowed}
#wx-paper-reason{font-size:11px!important;color:#a7c3ca!important;line-height:1.6!important;margin:0!important}
.wx-paper-divider{border-top:1px solid #54747744;margin-top:2px}
.wx-paper-selection{display:flex;flex-direction:column;gap:5px;font-size:10px;color:#aac4cc}
.wx-paper-selection strong{font-size:11px;color:#d7eef2;overflow-wrap:anywhere}
.wx-paper-available{margin:0}
.wx-paper-available summary{color:#a4e2df;font-size:11px;font-weight:750}
.wx-paper-available .wx-buckets{max-height:245px}
.wx-paper-note{margin-top:auto;border-top:1px solid #50757635;padding-top:12px}
.wx-paper-note strong{font-size:11px;color:#bde4df}
.wx-paper-note p{font-size:10px!important;color:#a3b6c1!important;margin:6px 0 0!important;line-height:1.55!important}
.wx-trade-quote .wx-hourly-quote-state{font-size:10px}
@media(max-width:1100px){
 .wx-trade-grid>.wx-panel{padding:17px}
 .wx-trade-quote{gap:12px}
 .wx-paper-side-pills button,.wx-paper-auto{padding:10px 13px}
 .wx-trade-price{padding:0 9px}
}
@media(max-width:920px){
 .wx-trade-grid{grid-template-columns:1fr;min-height:0}
 .wx-trade-grid>.wx-panel{min-height:0}
 .wx-paper-note{margin-top:12px}
}
@media(max-width:550px){
 .wx-trade-head{flex-wrap:wrap}
 .wx-trade-city-label select{max-width:100%}
 .wx-trade-prices{padding:12px 5px}
 .wx-trade-stat-label{font-size:10px}
 .wx-trade-price strong{font-size:24px}
 .wx-trade-time-caption{font-size:10px}
 .wx-trade-timing{padding:10px 11px;gap:7px}
 .wx-paper-side-pills button,.wx-paper-auto{padding:9px 11px;font-size:11px}
}

"""

MAIN = """
<div class="wx-trade-grid">
 <article class="wx-trade-forecast wx-panel">
  <div class="wx-trade-head">
   <span class="wx-trade-eyebrow">THIS HOUR’S FORECAST</span>
   <label class="wx-trade-city-label" for="wx-hourly-city">
    <span>City</span>
    <select id="wx-hourly-city" aria-label="Hourly Index city">
     <option value="chicago">Chicago Metro</option>
     <option value="nyc">New York City</option>
     <option value="miami">Miami</option>
     <option value="la-coastal">Coastal Los Angeles</option>
    </select>
   </label>
  </div>
  <div class="wx-trade-position">
   <strong>NO WEATHER POSITION</strong><span>Weather orders are disabled · research only</span>
  </div>
  <div class="wx-trade-prices">
   <div class="wx-trade-price">
    <div class="wx-trade-stat-label">TEMP TO BEAT <span title="Selected actual Kalshi YES threshold" aria-label="Selected actual Kalshi YES threshold">ⓘ</span></div>
    <strong id="wx-hourly-contract">—</strong>
    <small>Selected listed YES threshold · not the predicted temperature</small>
   </div>
   <div class="wx-trade-price wx-trade-price-current">
    <div class="wx-trade-stat-label">CURRENT INDEX <span title="Latest official Kalshi Weather Index reading" aria-label="Latest official Kalshi Weather Index reading">ⓘ</span></div>
    <strong id="wx-hourly-current">—</strong>
    <b id="wx-hourly-delta">—</b>
    <small>Kalshi Weather Index · not an airport thermometer</small>
   </div>
  </div>
  <div class="wx-trade-timing">
   <div class="wx-trade-time-left">
    <span class="wx-trade-time-icon" aria-hidden="true">◴</span>
    <div><span class="wx-trade-time-caption">ROUND CLOSES IN</span>
     <strong id="wx-hourly-close-clock" aria-live="off">—</strong></div>
   </div>
   <div class="wx-trade-time-right">
    <span class="wx-trade-time-caption">LATEST FORECAST UPDATE</span>
    <strong id="wx-hourly-last-forecast">—</strong>
   </div>
  </div>
  <div class="wx-trade-hero">
   <div id="wx-hourly-direction" class="wx-trade-direction" aria-live="polite">WAITING</div>
   <p class="wx-trade-direction-note">Forecast for the selected listed threshold · research estimate of the official index at the target hour.</p>
   <div class="wx-trade-confidence">
    <strong id="wx-hourly-confidence">—</strong>
    <div><b>Forecast probability</b><small id="wx-hourly-lean">Waiting for a listed contract</small></div>
   </div>
   <div class="wx-trade-progress" aria-hidden="true"><div id="wx-hourly-progress"></div></div>
   <p id="wx-hourly-model-chance" class="wx-trade-prob-explainer">Unvalidated modeled YES/NO probabilities are not exchange prices or historical accuracy.</p>
  </div>
  <div class="wx-trade-risk">
   <div><strong>REVERSAL RISK</strong><span id="wx-hourly-risk-badge">NOT VALIDATED</span></div>
   <p>No independently validated weather reversal-risk model. Forecast uncertainty is available in technical details.</p>
  </div>
  <div class="wx-trade-flip">
   <div><strong>FLIP ALERTS</strong><span id="wx-hourly-flips">WAITING FOR FORECAST</span></div>
   <p id="wx-hourly-flip-detail">Tracks a change in the selected ticker’s model-favored YES/NO side while this page is open. No push notifications.</p>
  </div>
  <p id="wx-hourly-diagnostic" class="wx-trade-diagnostic" role="status">Waiting for fresh official index data and matching hourly contracts.</p>
  <details class="wx-hourly-details"><summary>Forecast uncertainty and technical details</summary>
   <div class="wx-rows">
    <div class="wx-row"><span>90% modeled interval (not validated)</span><strong id="wx-hourly-interval">—</strong></div>
    <div class="wx-row"><span>Prediction status</span><strong id="wx-hourly-state">WAITING</strong></div>
    <div class="wx-row"><span>Learning evidence</span><strong id="wx-hourly-learning">WAITING</strong></div>
    <div class="wx-row"><span>Verified learning hours</span><strong id="wx-hourly-learning-hours">0</strong></div>
    <div class="wx-row"><span>Historical holdout scores</span><strong id="wx-hourly-learning-scores">NOT AVAILABLE</strong></div>
    <div class="wx-row"><span>Index nowcast evidence</span><strong id="wx-hourly-nowcast-state">WAITING</strong></div>
    <div class="wx-row"><span>Selected hourly prediction model</span><strong id="wx-hourly-nowcast-model">BASELINE</strong></div>
    <div class="wx-row"><span>Historical index hours</span><strong id="wx-hourly-nowcast-hours">0</strong></div>
    <div class="wx-row"><span>Nowcast lead-time model</span><strong id="wx-hourly-nowcast-lead">—</strong></div>
    <div class="wx-row"><span>Nowcast holdout (MAE / CRPS / interval coverage)</span><strong id="wx-hourly-nowcast-scores">NOT MEASURED</strong></div>
    <p>Research only; not independently certified. Historical holdout improvement does not guarantee live accuracy or trading profitability.</p>
    <div class="wx-row"><span>Prediction target (Eastern Time)</span><strong id="wx-hourly-et">—</strong></div>
    <div class="wx-row"><span>Target UTC</span><strong id="wx-hourly-utc">—</strong></div>
    <div class="wx-row"><span>Hourly Kalshi event</span><strong id="wx-hourly-event">—</strong></div>
    <div class="wx-row"><span>Last canonical minute (UTC)</span><strong id="wx-hourly-event-time">—</strong></div>
    <div class="wx-row"><span>Locally received (UTC)</span><strong id="wx-hourly-receipt">—</strong></div>
    <div class="wx-row"><span>Official index publisher</span><strong>Kalshi Weather Index</strong></div>
    <div class="wx-row"><span>HRRR constituent-station index guidance</span><strong id="wx-physics-hrrr">—</strong></div>
    <div class="wx-row"><span>NBM constituent-station index guidance</span><strong id="wx-physics-nbm">—</strong></div>
    <div class="wx-row"><span>HRRR + NBM physical blend (research)</span><strong id="wx-physics-blend">—</strong></div>
    <div class="wx-row"><span>Kalshi station calibration version</span><strong id="wx-physics-config">—</strong></div>
    <div class="wx-row"><span>Physical ensemble evidence</span><strong id="wx-physics-status">WAITING</strong></div>
    <div class="wx-row"><span>Physical ensemble validation</span><strong id="wx-physical-adaptive-state">COLLECTING</strong></div>
    <div class="wx-row"><span>Paired hourly forecast evidence</span><strong id="wx-physical-adaptive-pairs">0</strong></div>
    <div class="wx-row"><span>Completed hourly index outcomes</span><strong id="wx-physical-adaptive-scored">0</strong></div>
    <div class="wx-row"><span>Observed historical MAE (baseline / HRRR / NBM / blend)</span><strong id="wx-physical-adaptive-research-mae">NOT MEASURED</strong></div>
    <div class="wx-row"><span>Observed historical CRPS (baseline / HRRR / NBM / blend)</span><strong id="wx-physical-adaptive-research-crps">NOT MEASURED</strong></div>
    <div class="wx-row"><span>Prospective lockbox hours</span><strong id="wx-physical-adaptive-lockbox">0 / 32</strong></div>
    <div class="wx-row"><span>Settlement-verified lockbox hours</span><strong id="wx-physical-adaptive-verified">0 / 24</strong></div>
    <div class="wx-row"><span>Prospective MAE / CRPS / Brier</span><strong id="wx-physical-adaptive-metrics">NOT YET MEASURED</strong></div>
    <p>Physical guidance uses Kalshi-published station weights and Celsius offsets,
      NOAA model grids and AWC airport-coordinate proxies. The adaptive
      blend can change YES/NO model probabilities only after a fixed
      prospective, settlement-verified score gate. These limited historical
      scores are not independent calibration certification and are not a profit guarantee.</p>
   </div>
  </details>

  <details class="wx-hourly-details"><summary>Data feed status</summary>
   <div id="wx-hourly-health" class="wx-notice" role="status">Automatic hourly collectors initializing…</div>
  </details>
 </article>
 <article class="wx-trade-quote wx-panel">
  <div class="wx-paper-top">
   <div class="wx-paper-tabs" aria-label="Trade status"><strong>BUY</strong><span>SELL</span></div>
   <span class="wx-paper-badge">PAPER ONLY</span>
  </div>
  <div class="wx-paper-market-title">
   <span class="wx-paper-weather-symbol" aria-hidden="true">☁</span>
   <div><h2 id="wx-paper-market-name">Hourly Weather Index</h2>
    <small id="wx-paper-target">Waiting for listed contract</small></div>
  </div>
  <div class="wx-paper-side-pills" role="group" aria-label="Choose a side for paper estimate">
   <button type="button" id="wx-paper-yes" aria-pressed="true">YES —</button>
   <button type="button" id="wx-paper-no" aria-pressed="false">NO —</button>
   <span class="wx-paper-auto" title="No automatic weather orders">NO AUTO</span>
  </div>
  <label class="wx-paper-amount-label" for="wx-paper-amount">Amount for paper estimate (USD)</label>
  <div class="wx-paper-amount-wrap"><input id="wx-paper-amount" type="number" min="0.01" max="100000" step="0.01" inputmode="decimal" placeholder="Enter amount" autocomplete="off"><span>$</span></div>
  <p class="wx-paper-account">PAPER MARKET PREVIEW · no live balance or order permissions</p>
  <div class="wx-paper-metrics">
   <div><span>Selected quote (YES / NO)</span><strong id="wx-hourly-kalshi-ask">No verified ask</strong></div>
   <div><span>Quote status</span><strong id="wx-hourly-quote-state">Waiting for exchange quote</strong></div>
   <div><span>Indicative contracts · before fees</span><strong id="wx-paper-cost">—</strong></div>
   <div><span>Maximum gross payout · before fees</span><strong id="wx-paper-payout">—</strong></div>
  </div>
  <button id="wx-paper-order-disabled" type="button" disabled>Weather orders disabled</button>
  <p id="wx-paper-reason">Paper-only estimates use a recent Kalshi asking-price snapshot when available. No order will be placed.</p>
  <div class="wx-paper-divider"></div>
  <div class="wx-paper-selection"><span>Selected market</span><strong id="wx-hourly-focus-ticker">Not listed</strong></div>
  <details class="wx-hourly-details wx-paper-available">
   <summary>All listed hourly thresholds</summary>
   <div id="wx-hourly-ladder" class="wx-buckets wx-hourly-ladder" aria-live="polite">
    <div class="wx-empty">Waiting for matching hourly contracts.</div>
   </div>
  </details>
  <div class="wx-paper-note">
   <strong>Read-only WeatherEdge</strong>
   <p>Model probabilities remain unvalidated. Prices and sizes reflect exchange snapshots, not guaranteed fills; fees, liquidity and settlement rules matter. No automatic or manual weather trading is enabled.</p>
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
  <div class="wx-row"><span>Independently certified</span><strong>NOT CERTIFIED</strong></div>
  <div class="wx-row"><span>Live trading enabled</span><strong>NO</strong></div>
 </div>
 <div class="wx-hourly-details">
  <h3>Forward forecast accuracy · by city and lead</h3>
  <p>Each row represents a separate city, checkpoint horizon, and model.
   Verified hours count only exact official Index and matching settled
   Kalshi contract outcomes. Scores remain hidden until 10 verified hours.
   These are observed research metrics, not certified probabilities or
   proof of profitable trading.</p>
  <div id="wx-forward-cohorts" class="wx-forward-cohorts" aria-live="polite">
   <div class="wx-empty">Waiting for prospective verified forecasts…</div>
  </div>
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
 const el=id=>document.getElementById(id);
 const put=(id,value)=>{const node=el(id);if(node)node.textContent=String(value);};
 const f=value=>Number.isFinite(value)?value.toFixed(2)+" °F":"—";
 const t=value=>{if(!value)return "—";const d=new Date(value);
    return Number.isNaN(d.valueOf())?"—":d.toLocaleString("en-US",
      {timeZone:"America/New_York",year:"numeric",month:"short",day:"numeric",
       hour:"numeric",minute:"2-digit",timeZoneName:"short"});};
 const iso=value=>value||"—";
 const card=el("wx-hourly-city");
 let forecasts={};
 let activeCloseMs=null, serverClockBiasMs=0, activeLean="Model leans —";
 let marketListed=false, forecastApiOk=true;
 let activeQuoteReceivedMs=null, activeQuoteStatus="QUOTE_PENDING", activeAskText=null;
 let activeQuoteYesDollars=null, activeQuoteNoDollars=null;
 let activeMarketKey=null, previousMarketDirection=null, flipCount=0;
 let paperSide="YES", paperOnly=true;

 function fmtLocal(isoValue){
    if(!isoValue)return "—";
    const stamp=new Date(isoValue);
    if(!Number.isFinite(stamp.getTime()))return "—";
    return stamp.toLocaleString("en-US",{timeZone:"America/New_York",
      hour:"numeric",minute:"2-digit",timeZoneName:"short"});
 }

 function updatePaperPreview(){
    const yesButton=el("wx-paper-yes"),noButton=el("wx-paper-no");
    if(!yesButton||!noButton||!paperOnly)return;
    const serverNow=Date.now()-serverClockBiasMs;
    const fresh=activeQuoteStatus==="VALID_ASK" &&
      Number.isFinite(activeQuoteReceivedMs) &&
      serverNow>=activeQuoteReceivedMs && serverNow-activeQuoteReceivedMs<=35000 &&
      Number.isFinite(activeCloseMs) && serverNow<activeCloseMs;
    const quote=(value)=>fresh&&Number.isFinite(value)&&value>0&&value<=1?
      (value*100).toFixed(1)+"¢":"—";
    yesButton.textContent="YES "+quote(activeQuoteYesDollars);
    noButton.textContent="NO "+quote(activeQuoteNoDollars);
    yesButton.setAttribute("aria-pressed",String(paperSide==="YES"));
    noButton.setAttribute("aria-pressed",String(paperSide==="NO"));
    const current=paperSide==="YES"?activeQuoteYesDollars:activeQuoteNoDollars;
    const raw=el("wx-paper-amount")?.value;
    const amount=raw==null||raw.trim()===""?NaN:Number(raw);
    if(!fresh||!Number.isFinite(current)||current<=0||current>1){
       put("wx-paper-cost","—");put("wx-paper-payout","—");
       put("wx-paper-reason","No fresh verified "+paperSide+
         " asking-price snapshot with an open, timestamped market close. No paper estimate is available.");
       return;
    }
    if(!Number.isFinite(amount)||amount<.01||amount>100000){
       put("wx-paper-cost","—");put("wx-paper-payout","—");
       put("wx-paper-reason","Enter a USD amount to calculate an indicative "+
         paperSide+" paper estimate. No weather order will be placed.");
       return;
    }
    const qty=Math.max(0,Math.floor((amount+1e-9)/current));
    put("wx-paper-cost",qty+" whole contracts · $"+(qty*current).toFixed(2));
    put("wx-paper-payout","$"+qty.toFixed(2)+" gross");
    put("wx-paper-reason","Illustrative whole-contract quantity at a single observed ask, before fees. "+
      "Not an executable quote; posted size, slippage and fees are NOT modeled. No order will be placed.");
 }

 function tickQuote(){
    const serverNow=Date.now()-serverClockBiasMs;
    const fresh=Number.isFinite(activeQuoteReceivedMs) &&
      serverNow>=activeQuoteReceivedMs && serverNow-activeQuoteReceivedMs<=35000;
    const open=Number.isFinite(activeCloseMs) && serverNow<activeCloseMs;
    const status=!open?(Number.isFinite(activeCloseMs)?"CLOSED":"CLOSE_UNKNOWN"):
      activeQuoteStatus==="VALID_ASK"&&!fresh?
      "STALE_QUOTE":activeQuoteStatus;
    put("wx-hourly-kalshi-ask",status==="VALID_ASK"&&activeAskText?
      "YES / NO · "+activeAskText:"No fresh verified YES / NO ask");
    const readable=status==="NO_VISIBLE_ASK"?"No posted asking liquidity":
      status==="QUOTE_PENDING"?"Waiting for first exchange quote":
      status==="STALE_QUOTE"?"Quote expired; waiting for refresh":
      status==="QUOTE_UNAVAILABLE"?"Exchange quote unavailable":
      status==="CLOSED"?"Listed close time reached":
      status==="CLOSE_UNKNOWN"?"Market close time unavailable":status;
    put("wx-hourly-quote-state","Observed quote status · "+readable+
      (status==="VALID_ASK"?
         " · refreshed "+Math.max(0,Math.floor((serverNow-activeQuoteReceivedMs)/1000))+
         "s ago (not guaranteed executable)":""));
    updatePaperPreview();
 }
 function tickMarketClose(){
    tickQuote();
    if(!Number.isFinite(activeCloseMs)){
       put("wx-hourly-close-clock",!forecastApiOk?"FEED OFFLINE":
           marketListed?"CLOSE TIME UNKNOWN":"NO OPEN MARKET");
       put("wx-hourly-close-note",!forecastApiOk?
           "Official hourly forecast feed is unavailable.":
           marketListed?
           "Kalshi did not publish a valid closing timestamp; verify on Kalshi.":
           "No eligible open hourly contract has been verified. Temperature prediction still runs.");
       put("wx-hourly-lean",activeLean);
       return;
    }
    const remaining=Math.max(0,Math.ceil((activeCloseMs-(Date.now()-serverClockBiasMs))/1000));
    if(remaining===0){
       put("wx-hourly-close-clock","CLOSED");
       put("wx-hourly-close-note","Scheduled close reached · check Kalshi market status");
       put("wx-hourly-lean","Market closed — no current side");
       return;
    }
    const minutes=Math.floor(remaining/60);
    const seconds=String(remaining%60).padStart(2,"0");
    put("wx-hourly-close-clock",minutes+":"+seconds);
    put("wx-hourly-close-note","Kalshi-listed closing time · confirm on Kalshi; early closure is possible");
    put("wx-hourly-lean",activeLean);
 }
 function drawLadder(rows,focusTicker){
    const target=el("wx-hourly-ladder");if(!target)return;
    target.replaceChildren();
    if(!Array.isArray(rows)||rows.length===0){
       const div=document.createElement("div");div.className="wx-empty";
       div.textContent="No eligible hourly contract is currently listed for this forecast. Index predictions remain available.";
       target.append(div);return;
    }
    for(const x of rows){
       const p=Number(x.probability_yes);
       if(!Number.isFinite(p)||p<0||p>1)continue;
       const div=document.createElement("div");
       div.className="wx-bucket"+(x.ticker===focusTicker?" is-focus":"");
       const title=document.createElement("span");title.className="wx-bucket-label";
       title.textContent=x.threshold_f+" °F or above";
       const yes=document.createElement("span");yes.className="wx-hourly-yes";
       yes.textContent="YES "+(p*100).toFixed(1)+"%";
       const no=document.createElement("span");no.className="wx-hourly-no";
       no.textContent="NO "+((1-p)*100).toFixed(1)+"%";
       div.append(title,yes,no);target.append(div);
    }
 }
 function draw(){
    if(!card)return;
    const row=forecasts[card.value]||{};
    put("wx-hourly-state",row.status||"WAITING");
    const physics=row.physical_ensemble||{};
    put("wx-physics-hrrr",f(physics.hrrr_index_f));
    put("wx-physics-nbm",f(physics.nbm_index_f));
    put("wx-physics-blend",f(physics.ensemble_index_f));
    put("wx-physics-config",physics.calibration_version||"NOT AVAILABLE");
    put("wx-physics-status",physics.status||"NO_PHYSICAL_GUIDANCE");
    put("wx-physical-adaptive-state",row.physical_adaptive_status||"COLLECTING");
    put("wx-physical-adaptive-pairs",row.physical_adaptive_paired_hours??0);
    put("wx-physical-adaptive-scored",row.physical_adaptive_scored_hours??0);
    const research=row.physical_adaptive_research_scores;
    put("wx-physical-adaptive-research-mae",research?
        [research.baseline_mae_f,research.hrrr_mae_f,
         research.nbm_mae_f,research.equal_blend_mae_f].join(" / "):
        "Fewer than 10 completed paired hours");
    put("wx-physical-adaptive-research-crps",research?
        [research.baseline_crps,research.hrrr_crps,
         research.nbm_crps,research.equal_blend_crps].join(" / "):
        "Fewer than 10 completed paired hours");
    put("wx-physical-adaptive-lockbox",(row.physical_adaptive_lockbox_hours??0)+" / 32");
    put("wx-physical-adaptive-verified",(row.physical_adaptive_market_hours??0)+" / 24");
    const pm=row.physical_adaptive_metrics;
    put("wx-physical-adaptive-metrics",pm?
        "MAE "+pm.baseline_mae_f+" → "+pm.candidate_mae_f+" °F"+
        " · CRPS "+pm.baseline_crps+" → "+pm.candidate_crps+
        " · Brier "+pm.baseline_brier+" → "+pm.candidate_brier:
        "Not enough independently reconciled outcomes");
    put("wx-hourly-learning",row.learning_status||"WAITING FOR EVIDENCE");
    put("wx-hourly-learning-hours",row.learning_verified_hours??0);
    put("wx-hourly-nowcast-state",row.index_nowcast_status||"WAITING FOR INDEX");
    put("wx-hourly-nowcast-model",[
         "HISTORICAL_INDEX_NOWCAST_V2",
         "HISTORICAL_ADAPTIVE_INDEX_NOWCAST_V3"
         ].includes(row.model_kind)?
         (row.index_nowcast_selected_model||"UNKNOWN MODEL"):
         (row.model_kind||"BASELINE"));
    put("wx-hourly-nowcast-hours",row.index_nowcast_history_hours??0);
    put("wx-hourly-nowcast-lead",row.index_nowcast_lead_minutes!=null?
        row.index_nowcast_lead_minutes+" minutes":"—");
    const index_nowcast_validation=row.index_nowcast_validation;
    put("wx-hourly-nowcast-scores",index_nowcast_validation?
        "MAE "+index_nowcast_validation.baseline_mae_f+" → "+
                   index_nowcast_validation.candidate_mae_f+" °F"+
        " · CRPS "+index_nowcast_validation.baseline_crps+" → "+
                   index_nowcast_validation.candidate_crps+
        " · 90% coverage "+
                   Math.round(index_nowcast_validation.candidate_90_coverage*100)+"% (historical)":
        "Insufficient point-in-time hourly index cases");
    const validation=row.learning_validation;
    put("wx-hourly-learning-scores",validation?
        "Brier "+validation.baseline_brier+" → "+validation.candidate_brier+
        " · MAE "+validation.baseline_mae_f+" → "+validation.candidate_mae_f+" °F"+
        " · 90% coverage "+Math.round(100*validation.candidate_90_coverage)+"% (historical)":
        "Not enough verified settled hours");
    put("wx-hourly-current",f(row.current_index_f));
    put("wx-hourly-mean",f(row.predicted_index_f));
    put("wx-hourly-last-forecast",fmtLocal(row.asof));
    const labels={"chicago":"Chicago Metro","nyc":"New York City",
       "miami":"Miami","la-coastal":"Coastal Los Angeles"};
    const named=labels[card.value]||"Hourly Weather Index";
    put("wx-paper-market-name",named+" · Temperature");
    put("wx-paper-target",row.target_utc?
      "Hourly target "+fmtLocal(row.target_utc)+" · official Kalshi Index":
      "Waiting for the next published hourly target");
    put("wx-hourly-interval",Array.isArray(row.interval_90_f)?
        row.interval_90_f.map(f).join(" – "):"NOT MEASURED");
    put("wx-hourly-utc",iso(row.target_utc));
    put("wx-hourly-et",t(row.target_utc));
    put("wx-hourly-event",iso(row.event_ticker));
    put("wx-hourly-event-time",Number.isFinite(row.source_event_ms)?
        new Date(row.source_event_ms).toISOString():"—");
    put("wx-hourly-receipt",iso(row.source_received_at));
    const serverTime=Date.parse(row.asof||"");
    serverClockBiasMs=Number.isFinite(serverTime)?Date.now()-serverTime:0;
    const ready=row.status==="READY_RESEARCH" && Number.isFinite(row.predicted_index_f);
    const strikes=ready && Array.isArray(row.markets)?row.markets.filter(x=>
        Number.isFinite(x.threshold_f) && Number.isFinite(x.probability_yes) &&
        x.probability_yes>=0 && x.probability_yes<=1):[];
    // Choose the nearest actual listed threshold, not a fabricated price or strike.
    // Prefer a contract with a published close time for a usable countdown.
    const ordered=strikes.slice().sort((a,b)=>{
       const da=Math.abs(a.threshold_f-row.predicted_index_f);
       const db=Math.abs(b.threshold_f-row.predicted_index_f);
       if(da!==db)return da-db;
       const ac=Number.isFinite(Date.parse(a.close_time_utc||""));
       const bc=Number.isFinite(Date.parse(b.close_time_utc||""));
       return Number(bc)-Number(ac);
    });
    const focus=ordered[0];
    marketListed=Boolean(focus);
    const asks=(value,size)=>typeof value==="string"&&typeof size==="string"?
      (100*Number(value)).toFixed(1)+"¢ (size "+size+")":"no visible ask";
    activeQuoteStatus=focus?.quote_status||"QUOTE_PENDING";
    activeQuoteYesDollars=(typeof focus?.yes_ask_dollars==="string" &&
      Number.isFinite(Number(focus.yes_ask_dollars)) &&
      Number(focus.yes_ask_dollars)>0 && Number(focus.yes_ask_dollars)<=1)?
      Number(focus.yes_ask_dollars):null;
    activeQuoteNoDollars=(typeof focus?.no_ask_dollars==="string" &&
      Number.isFinite(Number(focus.no_ask_dollars)) &&
      Number(focus.no_ask_dollars)>0 && Number(focus.no_ask_dollars)<=1)?
      Number(focus.no_ask_dollars):null;
    activeQuoteReceivedMs=focus?.quote_received_at?
      Date.parse(focus.quote_received_at):null;
    if(!Number.isFinite(activeQuoteReceivedMs))activeQuoteReceivedMs=null;
    activeAskText=focus?asks(focus.yes_ask_dollars,focus.yes_ask_size_fp)+
      " / "+asks(focus.no_ask_dollars,focus.no_ask_size_fp):null;
    activeCloseMs=focus && focus.close_time_utc?Date.parse(focus.close_time_utc):null;
    if(!Number.isFinite(activeCloseMs))activeCloseMs=null;
    activeLean="Waiting for a listed contract";
    put("wx-hourly-contract",focus?focus.threshold_f+"°F":"—");
    put("wx-hourly-focus-ticker",focus?"Kalshi ticker: "+focus.ticker:
       "No verified listed contract");
    const current=Number.isFinite(row.current_index_f)?row.current_index_f:null;
    const delta=focus && current!=null?current-focus.threshold_f:null;
    put("wx-hourly-delta",delta==null?"—":
       (Math.abs(delta)<.005?"At listed threshold":
        Math.abs(delta).toFixed(2)+"°F "+(delta>0?"above":"below")+" threshold"));
    const marketKey=focus?card.value+"|"+row.event_ticker+"|"+focus.ticker:null;
    const side=focus?focus.probability_yes>.5?"YES":
       focus.probability_yes<.5?"NO":null:null;
    if(marketKey!==activeMarketKey){
       activeMarketKey=marketKey;
       previousMarketDirection=null;
       flipCount=0;
    }
    if(marketKey && side && previousMarketDirection && side!==previousMarketDirection){
       flipCount++;
    }
    if(marketKey && side)previousMarketDirection=side;
    put("wx-hourly-flips",marketKey?(flipCount?
       flipCount+" FLIP"+(flipCount===1?"":"S")+" OBSERVED":"MONITORING"):"NO MARKET");
    put("wx-hourly-flip-detail",marketKey?
       (flipCount?"Model-favored side changed "+flipCount+" time(s) while this page was open.":
       "No model-side flip observed for this ticker in this browser session."):
       "No selected market to monitor. Browser-only observation; no push alerts.");
    const hero=el("wx-hourly-direction");
    if(hero){
       const label=side==="YES"?"ABOVE":side==="NO"?"BELOW":
          focus?"EVEN":"WAITING";
       hero.textContent=label;
       hero.className="wx-trade-direction "+(side==="YES"?"yes":
          side==="NO"?"no":"waiting");
    }
    if(focus){
       const yesPct=100*focus.probability_yes, noPct=100-yesPct;
       const modeled=Math.max(yesPct,noPct);
       put("wx-hourly-confidence",modeled.toFixed(1)+"%");
       const progress=el("wx-hourly-progress");
       if(progress)progress.style.width=modeled.toFixed(1)+"%";
       activeLean=side===null?"Model is evenly split":
          "Model leans "+(side==="YES"?"YES (at least threshold)":"NO (below threshold)");
       put("wx-hourly-model-chance","Modeled YES "+yesPct.toFixed(1)+"% · NO "+
          noPct.toFixed(1)+"% · unvalidated research probabilities, NOT Kalshi asking prices.");
    }else{
       put("wx-hourly-confidence","—");
       const progress=el("wx-hourly-progress");
       if(progress)progress.style.width="0%";
       put("wx-hourly-model-chance",row.predicted_index_f!=null?
         "Temperature forecast available; no verified YES/NO contract.":
         "Waiting for a reliable hourly temperature prediction.");
    }
    tickMarketClose();
    put("wx-hourly-diagnostic",row.status==="READY_RESEARCH"?
       "Model view only. Match the exact ticker on Kalshi and check its live price before making any decision.":
       row.status==="NO_VERIFIED_HOURLY_MARKETS"?
       "Index is available, but no verified open Kalshi hourly contract ladder was found.":
       row.status==="STALE_INDEX"?
       "Official index data are stale; prediction and contract side are withheld.":
       "Waiting for fresh official readings and matching hourly contracts.");
    drawLadder(strikes,focus?focus.ticker:null);
 }
 async function update(){
   try{
      const res=await fetch("/api/weather/hourly/forecast",{cache:"no-store"});
      if(!res.ok)throw new Error("Hourly forecast API unavailable");
      const data=await res.json();
      forecasts={};
      for(const row of data.forecasts||[])forecasts[row.city]=row;
      forecastApiOk=true;
      draw();
   }catch(err){forecastApiOk=false;forecasts={};draw();put("wx-hourly-state","FEED UNAVAILABLE");}
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
      const forward=c.forward_v9||{};
      const forwardTarget=el("wx-forward-cohorts");
      if(forwardTarget){
        forwardTarget.replaceChildren();
        const intro=document.createElement("div");
        intro.className="wx-forward-cohort";
        intro.textContent="V9 evidence: "+(forward.status||"NOT STARTED")+
          " · frozen "+(forward.total_frozen??0)+
          " · source-matched "+(forward.total_verified??0);
        forwardTarget.append(intro);
        const cohorts=Array.isArray(forward.groups)?forward.groups:[];
        const score=(value,digits=4)=>Number.isFinite(value)?
            Number(value).toFixed(digits):"NOT MEASURED";
        for(const cohort of cohorts.slice(0,30)){
          const line=document.createElement("div");
          line.className="wx-forward-cohort";
          const model=typeof cohort.model_kind==="string"?cohort.model_kind:"UNKNOWN";
          const city=typeof cohort.city==="string"?cohort.city:"UNKNOWN";
          line.textContent=city+" · "+(cohort.lead_bucket??"?")+" min"+
            " · "+model+" · Verified hours "+(cohort.verified_hours??0)+
            " / "+(cohort.frozen??0)+" frozen"+
            " · MAE "+score(cohort.mae_f,2)+" °F"+
            " · Brier "+score(cohort.brier)+
            " · Log loss "+score(cohort.log_loss)+
            " · 90% coverage "+(Number.isFinite(cohort.coverage_90)?
               (100*cohort.coverage_90).toFixed(1)+"%":"NOT MEASURED");
          forwardTarget.append(line);
        }
      }
      const h=s.collector||{},feed=h.hourly||{},outcome=h.hourly_settlement||{};
      const physical=h.hourly_physics||{};
      const quotes=h.hourly_quotes||{};
      put("wx-hd-settlement",outcome.state||"NOT STARTED");
      const summary=["Automatic Kalshi hourly collector: "+(h.state||"STARTING")];
      for(const city of ["chicago","nyc","miami","la-coastal"]){
        const x=feed[city]||{};
        summary.push(city+": "+(x.state||"WAITING")+
          (Number.isFinite(x.source_market_count)?
            " · Kalshi open: "+x.source_market_count+
            " · accepted: "+(x.market_strikes??0):"")+
          (Array.isArray(x.listed_event_tickers)&&x.listed_event_tickers.length?
            " · events: "+x.listed_event_tickers.join(", "):"")+
          (x.fallback_state&&x.fallback_state!=="NOT_NEEDED"?
             " · events fallback: "+x.fallback_state+
             " · nested markets: "+(x.fallback_market_count??0):"")+
          (x.reason_code?" · "+x.reason_code:"")+
          (x.reason?" ("+x.reason+")":""));
      }
      summary.push("Kalshi quote feed: "+(quotes.state||"WAITING")+
        " · current asks: "+(quotes.valid_asks??0)+
        " · no visible liquidity: "+(quotes.no_visible_ask??0)+
        " · errors: "+(quotes.unavailable??0)+
        (quotes.reason?" ("+quotes.reason+")":""));
      summary.push("Physical HRRR/NBM: "+(physical.state||"WAITING")+
        (physical.cities&&physical.cities[card?.value]?
          " · "+(physical.cities[card.value].state||"WAITING")+
          " · "+(physical.cities[card.value].configuration||"NO_CONFIG"):"")+
        (physical.reason?" ("+physical.reason+")":""));
      summary.push("Settlement: "+(outcome.state||"WAITING")+
         (outcome.verified!=null?" · newly matched "+outcome.verified:""));
      put("wx-hourly-health",summary.join(" · "));
      const selectedHealth=feed[card?.value]||{};
      if(selectedHealth.state==="NO_ACTIVE_HOURLY_MARKET"){
         const upcoming=selectedHealth.next_listed_target_utc?
           " · next listed event: "+t(selectedHealth.next_listed_target_utc)+
           " (outside short-horizon forecast)":"";
         put("wx-hourly-diagnostic",
           "Market discovery: "+(selectedHealth.reason||
             "No eligible future hourly Kalshi event is currently listed.")+
           " · Kalshi open: "+(selectedHealth.source_market_count??"unknown")+
           " · in-horizon events: "+(selectedHealth.future_event_count??"unknown")+
           " · second source: "+(selectedHealth.fallback_state||"NOT CHECKED")+
           upcoming);
      }
   }catch(err){
      put("wx-hourly-health","Hourly index health endpoint unavailable.");
      put("wx-hm-state","EVIDENCE API UNAVAILABLE");
   }
 }
 if(card)card.addEventListener("change",draw);
 const yesButton=el("wx-paper-yes"),noButton=el("wx-paper-no");
 if(yesButton)yesButton.addEventListener("click",()=>{paperSide="YES";updatePaperPreview();});
 if(noButton)noButton.addEventListener("click",()=>{paperSide="NO";updatePaperPreview();});
 const paperAmount=el("wx-paper-amount");
 if(paperAmount)paperAmount.addEventListener("input",updatePaperPreview);
 update();setInterval(update,15000);setInterval(tickMarketClose,1000);
})();
</script>"""


def render_hourly_page(section: str, weather_style: str) -> str:
    if section not in PAGES:
        raise ValueError("Unknown WeatherEdge hourly page")
    base_css = PAGE.split("<style>",1)[1].split("</style>",1)[0]
    css = "\n".join((base_css,STYLE,MOTION_STYLE,MARKET_SELECTOR_STYLE,weather_style,HOURLY_STYLE))
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
