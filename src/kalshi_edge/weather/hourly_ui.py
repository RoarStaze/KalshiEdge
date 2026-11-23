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
@media(max-width:650px){.wx-hourly-overview{grid-template-columns:1fr}.wx-hourly-ladder .wx-bucket{gap:6px}}
"""

MAIN = """
<div class="wx-grid">
 <article class="wx-panel">
  <div class="wx-prediction-heading"><h2>Hourly weather prediction</h2>
   <label class="wx-picker-label" for="wx-hourly-city">City
    <select id="wx-hourly-city" aria-label="Hourly index city">
     <option value="chicago">Chicago Metro · KXTEMPCHIHS</option>
     <option value="nyc">New York City · KXTEMPNYCHS</option>
     <option value="miami">Miami · KXTEMPMIAH</option>
     <option value="la-coastal">Coastal Los Angeles · KXTEMPLAXHS</option>
    </select></label></div>
  <div class="wx-stats wx-hourly-overview">
   <div class="wx-stat wx-hourly-primary">
    <span>Predicted temperature</span><strong id="wx-hourly-mean">—</strong>
    <small>Forecast for the selected hour · Kalshi Weather Index</small>
   </div>
   <div class="wx-stat wx-hourly-clock">
    <span>Market closes in</span><strong id="wx-hourly-close-clock" aria-live="off">—:—</strong>
    <small id="wx-hourly-close-note">Kalshi-listed closing time · confirm on Kalshi; early closure is possible</small>
   </div>
   <div class="wx-stat wx-hourly-contract">
    <span>Closest Kalshi contract</span><strong id="wx-hourly-contract">—</strong>
    <b id="wx-hourly-lean">Model leans —</b>
    <small id="wx-hourly-model-chance">Waiting for an open matching Kalshi market</small>
    <small id="wx-hourly-kalshi-ask">Kalshi ask YES / NO · waiting for verified quote</small>
    <small id="wx-hourly-quote-state">Observed quote status · waiting</small>
    <small id="wx-hourly-focus-ticker"></small>
   </div>
   <div class="wx-stat wx-hourly-current-stat">
    <span>Current official index</span><strong id="wx-hourly-current">—</strong>
    <small>Latest published index value, not the prediction</small>
   </div>
  </div>
  <p class="wx-hourly-caution">Calibration not verified. Model probabilities are research estimates, not Kalshi asking prices or proof of a profitable bet. Verified asks are exchange snapshots, not guaranteed executable, and fees, liquidity and fill risk matter. No automatic weather trading.</p>
  <details class="wx-hourly-details"><summary>Forecast uncertainty and technical details</summary>
   <div class="wx-rows">
    <div class="wx-row"><span>90% modeled interval (not validated)</span><strong id="wx-hourly-interval">—</strong></div>
    <div class="wx-row"><span>Prediction status</span><strong id="wx-hourly-state">WAITING</strong></div>
    <div class="wx-row"><span>Learning evidence</span><strong id="wx-hourly-learning">WAITING</strong></div>
    <div class="wx-row"><span>Verified learning hours</span><strong id="wx-hourly-learning-hours">0</strong></div>
    <div class="wx-row"><span>Historical holdout scores</span><strong id="wx-hourly-learning-scores">NOT AVAILABLE</strong></div>
    <div class="wx-row"><span>Index nowcast evidence</span><strong id="wx-hourly-nowcast-state">WAITING</strong></div>
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
  <p id="wx-hourly-diagnostic" role="status">Waiting for fresh index data and matching hourly contracts.</p>
  <details class="wx-hourly-details"><summary>Data feed status</summary>
   <div id="wx-hourly-health" class="wx-notice" role="status">Automatic hourly collectors initializing…</div>
  </details>
 </article>
 <article class="wx-panel">
  <h2>Kalshi hourly markets · model chances</h2>
  <p>Each row is one listed Kalshi YES/NO contract. See which outcome the model favors, then compare with the actual price on Kalshi. These chances are not validated.</p>
  <div id="wx-hourly-ladder" class="wx-buckets wx-hourly-ladder" aria-live="polite">
   <div class="wx-empty">Waiting for matching hourly contracts.</div>
  </div>
  <div class="wx-rows">
   <div class="wx-row"><span>Confidence evidence</span><strong>NOT VALIDATED</strong></div>
   <div class="wx-row"><span>Live weather orders</span><strong>DISABLED</strong></div>
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
 function tickQuote(){
    const serverNow=Date.now()-serverClockBiasMs;
    const fresh=Number.isFinite(activeQuoteReceivedMs) &&
      serverNow>=activeQuoteReceivedMs && serverNow-activeQuoteReceivedMs<=35000;
    const status=activeQuoteStatus==="VALID_ASK"&&!fresh?
      "STALE_QUOTE":activeQuoteStatus;
    put("wx-hourly-kalshi-ask",status==="VALID_ASK"&&activeAskText?
      "Kalshi ask YES / NO · "+activeAskText:
      "Kalshi ask YES / NO · no current verified executable ask");
    put("wx-hourly-quote-state","Observed quote status · "+status+
      (status==="VALID_ASK"?
         " · refreshed "+Math.max(0,Math.floor((serverNow-activeQuoteReceivedMs)/1000))+
         "s ago (not guaranteed executable)":""));
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
    activeQuoteReceivedMs=focus?.quote_received_at?
      Date.parse(focus.quote_received_at):null;
    if(!Number.isFinite(activeQuoteReceivedMs))activeQuoteReceivedMs=null;
    activeAskText=focus?asks(focus.yes_ask_dollars,focus.yes_ask_size_fp)+
      " / "+asks(focus.no_ask_dollars,focus.no_ask_size_fp):null;
    activeCloseMs=focus && focus.close_time_utc?Date.parse(focus.close_time_utc):null;
    if(!Number.isFinite(activeCloseMs))activeCloseMs=null;
    activeLean="Model leans —";
    put("wx-hourly-contract",focus?focus.threshold_f+" °F or above":"No listed market");
    put("wx-hourly-focus-ticker",focus?"Kalshi ticker: "+focus.ticker:"");
    if(focus){
       const yesPct=100*focus.probability_yes, noPct=100-yesPct;
       activeLean=Math.abs(yesPct-noPct)<0.01?"Model is evenly split":
          "Model leans "+(yesPct>noPct?"YES":"NO");
       put("wx-hourly-model-chance","Estimated YES "+yesPct.toFixed(1)+
          "% · NO "+noPct.toFixed(1)+"% (unvalidated)");
    }else{
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
