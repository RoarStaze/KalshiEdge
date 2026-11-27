"""Bitcoin History visual language for read-only hourly WeatherEdge evidence.

All dynamic source values are inserted using textContent, never innerHTML.
"""
from __future__ import annotations

HISTORY_STYLE = r"""
.wx-shell[data-weather-section="history"]{max-width:none;margin-top:12px;gap:17px}
.wx-shell[data-weather-section="history"]>.wx-heading{display:none}
.wx-history{display:grid;gap:16px}
.wx-history-meta{min-height:25px;display:flex;align-items:center;
  justify-content:space-between;gap:12px;flex-wrap:wrap;border-top:1px solid #8ca7a52b;
  padding-top:7px;color:#aebbc3;font-size:11px}
.wx-history-meta .wx-history-marker{font-size:10px;letter-spacing:1px;
  font-weight:800;color:#a4e6d1}
.wx-history-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.wx-history-kpi{min-width:0;min-height:132px;display:flex;flex-direction:column;
  justify-content:center;padding:17px 18px;
  border:1px solid transparent;border-radius:18px;
  background:linear-gradient(145deg,rgba(17,33,35,.93),rgba(2,29,29,.93)) padding-box,
    conic-gradient(from var(--ke-rainbow-angle),#39f9c4,#338ce2,#ed3fff,#eac87c,#39f9c4) border-box;
  animation:ke-rainbow-cycle 8s linear infinite}
.wx-history-kpi-label{font-size:11px;color:#b9c9d0;line-height:1.4}
.wx-history-kpi-num{display:block;font-size:clamp(24px,2.6vw,34px);line-height:1.2;
  color:#eff5f8;font-variant-numeric:tabular-nums;margin:13px 0 6px;
  font-weight:850;letter-spacing:-.6px;overflow-wrap:anywhere}
.wx-history-kpi-num.is-accuracy{color:#29e5ad}
.wx-history-kpi-caption{font-size:10px;color:#a4b9c1;line-height:1.5}
.wx-history-section{min-width:0;overflow:hidden;border:1px solid transparent;border-radius:17px;
  background:linear-gradient(155deg,#172a2c,#101e22 95%) padding-box,
    conic-gradient(from var(--ke-rainbow-angle),#e3bb5d,#3df9b5,#4bc0ed,#e248d1,#e3bb5d) border-box;
  animation:ke-rainbow-cycle 10s linear infinite}
.wx-history-controls{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:14px;padding:18px 20px;background:#202a2780;
  border-bottom:1px solid #67878c4a}
.wx-history-controls h2{font-size:14px;font-weight:800;margin:0;color:#f4faf5}
.wx-history-controls label{display:flex;align-items:center;gap:9px;
  font-size:11px;color:#aebfc9}
.wx-history-controls select{min-width:130px;max-width:190px;border:1px solid #64878f73;
  background:#14232b;color:#f2faff;border-radius:9px;padding:8px 10px;font-size:11px;
  cursor:pointer}
.wx-history-table-wrap{overflow-x:auto;max-width:100%;max-height:calc(100vh - 380px);
  min-height:220px}
.wx-history-table{width:100%;min-width:970px;border-collapse:collapse;font-size:11px}
.wx-history-table th{text-align:left;white-space:nowrap;font-size:10px;color:#a8c9d2;
  text-transform:uppercase;letter-spacing:.9px;font-weight:750;
  padding:15px 15px;background:#14272c}
.wx-history-table td{padding:14px 15px;color:#dbe8ec;border-top:1px solid #51747846;
  font-variant-numeric:tabular-nums;vertical-align:middle;white-space:nowrap}
.wx-history-table tbody tr:nth-child(odd){background:#2a342d87}
.wx-history-table tbody tr:nth-child(even){background:#1b2b2cba}
.wx-history-table tbody tr:hover{background:#27423e}
.wx-history-table .wx-history-ticker{display:block;font-size:10px;color:#c4d9dc;
  font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.wx-history-table .wx-history-city{display:block;font-size:10px;color:#9fc9c1;margin-bottom:5px}
.wx-history-pill{display:inline-block;border-radius:6px;border:1px solid #536578;
  padding:4px 8px;font-size:10px;font-weight:850;letter-spacing:.4px}
.wx-history-pill.ABOVE,.wx-history-pill.CORRECT{color:#56e8ba;border-color:#366e63;background:#143a33}
.wx-history-pill.BELOW,.wx-history-pill.INCORRECT{color:#ff8aa5;border-color:#92516e;background:#3c2533}
.wx-history-pill.SKIP{color:#f3c476;border-color:#815f30;background:#372b1d}
.wx-history-pill.PENDING{color:#b5c9f7;border-color:#576a9c;background:#222b47}
.wx-history-pill.FETCH_ERROR{color:#ffc98a;border-color:#93612c;background:#43311c}
.wx-history-pill.AWAITING_FINALIZATION{color:#b5c9f7;border-color:#576a9c;background:#222b47}
.wx-history-pill.INVALID_OFFICIAL_EVIDENCE{color:#ff9fba;border-color:#92516e;background:#3c2533}
.wx-history-pill.EVEN,.wx-history-pill.UNSCORED{color:#c6d5d4;border-color:#4f6971;background:#28353b}
.wx-history-table .wx-history-empty{padding:26px;color:#9fb8c2;text-align:center;white-space:normal}
.wx-history-footnote{font-size:11px;line-height:1.7;color:#9ebac2;
  padding:3px 3px 10px}
.wx-history-alert{border-left:3px solid #e3a55d;padding:10px 14px;
  background:#35271c8c;font-size:12px;color:#f4d5ac;border-radius:8px}
@media(max-width:1180px){
 .wx-history-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
 .wx-history-table-wrap{max-height:none}
}
@media(max-width:620px){
 .wx-history-kpis{grid-template-columns:1fr 1fr;gap:8px}
 .wx-history-kpi{min-height:114px;padding:13px}
 .wx-history-kpi-num{font-size:24px}
 .wx-history-controls{padding:13px}
 .wx-history-table th,.wx-history-table td{padding:12px 11px}
}
@media(max-width:380px){.wx-history-kpis{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){
 .wx-history-kpi,.wx-history-section{animation:none}
}
"""

HISTORY = """
<div class="wx-history" aria-label="Hourly Weather Index forecast history">
 <div class="wx-history-meta">
  <span id="wx-history-status" role="status" aria-live="polite">Loading recorded weather forecasts…</span>
  <span class="wx-history-marker">KALSHI WEATHER INDEX · PAPER ONLY</span>
 </div>
 <div class="wx-history-kpis" aria-label="Weather forecast outcome summary">
  <article class="wx-history-kpi">
   <span class="wx-history-kpi-label">Settled forecast accuracy</span>
   <strong class="wx-history-kpi-num is-accuracy" id="wx-history-accuracy">—</strong>
   <span class="wx-history-kpi-caption" id="wx-history-accuracy-detail">Observed verified results · small sample until 10 hours</span>
  </article>
  <article class="wx-history-kpi">
   <span class="wx-history-kpi-label">Correct / Incorrect</span>
   <strong class="wx-history-kpi-num" id="wx-history-correctness">0 / 0</strong>
   <span class="wx-history-kpi-caption">Only verified settled ABOVE/BELOW forecasts</span>
  </article>
  <article class="wx-history-kpi">
   <span class="wx-history-kpi-label">SKIP decisions</span>
   <strong class="wx-history-kpi-num" id="wx-history-skips">0</strong>
   <span class="wx-history-kpi-caption">SKIP decisions are not recorded by the hourly collector</span>
  </article>
  <article class="wx-history-kpi">
   <span class="wx-history-kpi-label">Pending settlement</span>
   <strong class="wx-history-kpi-num" id="wx-history-pending">0</strong>
   <span class="wx-history-kpi-caption">Never counted as incorrect</span>
  </article>
 </div>
 <div class="wx-history-section">
  <div class="wx-history-controls">
   <h2>Recorded hourly forecasts</h2>
   <label for="wx-history-filter">Show
    <select id="wx-history-filter" aria-label="Filter recorded hourly forecasts">
     <option value="ALL">All forecasts</option>
     <option value="CORRECT">Correct</option>
     <option value="INCORRECT">Incorrect</option>
     <option value="PENDING">Pending</option>
     <option value="SKIP">SKIP</option>
     <option value="UNSCORED">Unscored / even</option>
    </select>
   </label>
  </div>
  <div class="wx-history-table-wrap" tabindex="0" role="region" aria-label="Scroll weather history table">
   <table class="wx-history-table">
    <thead><tr>
     <th scope="col">Market closes (local)</th>
     <th scope="col">Market</th>
     <th scope="col">Forecast</th>
     <th scope="col">Probability</th>
     <th scope="col">Confidence</th>
     <th scope="col">Settled outcome</th>
     <th scope="col">Result</th>
    </tr></thead>
    <tbody id="wx-history-rows">
     <tr><td colspan="7" class="wx-history-empty">Loading recorded forecasts…</td></tr>
    </tbody>
   </table>
  </div>
 </div>
 <div class="wx-history-footnote">
  Only the latest immutable precommitted checkpoint per city and hour is shown (legacy first-frozen records are used when V9 is absent).
  Accuracy uses verified official Kalshi Index and matching settled contract results.
  Pending and unscored records are excluded. Confidence is derived from the
  saved predicted-side probability: 2 × probability − 1, the same directional
  strength formula displayed on Bitcoin History. This is not observed win rate
  or independently validated confidence. Early percentages are a small sample
  until at least 10 distinct settled hours and do not certify calibration.
  Source evidence: INDEX_RECONCILED_SETTLEMENT means official Kalshi Index
  and settled contract agreed; KALSHI_FINALIZED_MARKET means the exact official
  market's finalized result was verified without independent index reconciliation.
  Pending means the market is upcoming or official final verification is still
  outstanding; FEED ERROR and VERIFY ERROR identify failures rather than
  pretending the exchange has not settled. Forecast probabilities are unvalidated research estimates; this
  is not proof of profitable trading. No weather orders.
 </div>
</div>
"""

HISTORY_SCRIPT = r"""<script>
(() => {
 "use strict";
 const byId=id=>document.getElementById(id);
 const set=(id,value)=>{const n=byId(id);if(n)n.textContent=String(value);};
 const cityLabels={"chicago":"Chicago Metro","nyc":"New York City",
   "miami":"Miami","la-coastal":"Coastal Los Angeles"};
 const percent=n=>typeof n==="number"&&Number.isFinite(n)?
   (n*100).toFixed(2)+"%":"—";
 const localTime=s=>{if(typeof s!=="string"||!s)return "Close time not recorded";
   const ms=Date.parse(s);return Number.isFinite(ms)?
     new Date(ms).toLocaleString():"Close time not recorded";};
 const safeString=x=>typeof x==="string"?x:"—";
 let history=null,refreshing=false;
 const filter=byId("wx-history-filter");
 const tbody=byId("wx-history-rows");

 function td(tr,value){
   const cell=document.createElement("td");cell.textContent=String(value);
   tr.append(cell);return cell;
 }
 function badge(tr,value,label){
   const cell=document.createElement("td");
   const chip=document.createElement("span");
   const raw=safeString(value);
   const kind=["ABOVE","BELOW","CORRECT","INCORRECT","SKIP",
               "PENDING","EVEN","UNSCORED","FETCH_ERROR",
               "AWAITING_FINALIZATION","INVALID_OFFICIAL_EVIDENCE"].includes(raw)?raw:"UNSCORED";
   chip.className="wx-history-pill "+kind;
   chip.textContent=label||raw;cell.append(chip);tr.append(cell);
 }
 function render(){
   if(!tbody||!history)return;
   const data=history,summary=data.summary||{};
   const valid=data.status!=="HOURLY_HISTORY_AUDIT_ERROR";
   const good=Number(summary.correct)||0,bad=Number(summary.incorrect)||0;
   set("wx-history-accuracy",valid?percent(summary.accuracy):"UNAVAILABLE");
   set("wx-history-accuracy-detail",valid?
     (summary.settled_directional_forecasts||0)+
       " verified directional forecasts"+(summary.accuracy==null?
         " · awaiting verified settlements":
         (summary.settled_directional_forecasts||0)<10?
           " · small sample (not calibrated)":""):
     "Source audit failed; scores withheld");
   set("wx-history-correctness",valid?good+" / "+bad:"— / —");
   set("wx-history-skips",valid?(summary.skipped??0):"—");
   set("wx-history-pending",valid?(summary.pending_settlement??0):"—");
   set("wx-history-status",valid?
     (summary.total_final_forecasts||0)+" records · Refreshed "+
       new Date().toLocaleTimeString()+(Array.isArray(data.forecasts)&&
       summary.total_final_forecasts>data.forecasts.length?
       " · displaying latest "+data.forecasts.length:""):
     "FORECAST HISTORY AUDIT ERROR · "+safeString(data.reason));
   tbody.replaceChildren();
   if(!valid){
     const tr=document.createElement("tr");
     const c=td(tr,"Source integrity check failed; history and scores withheld.");
     c.colSpan=7;c.className="wx-history-empty";tbody.append(tr);return;
   }
   const wanted=filter?.value||"ALL";
   const records=Array.isArray(data.forecasts)?data.forecasts:[];
   const rows=records.filter(x=>wanted==="ALL"||x.verdict===wanted);
   if(!rows.length){
     const tr=document.createElement("tr");
     const c=td(tr,"No matching forecasts. New verified hourly records appear automatically after collection.");
     c.colSpan=7;c.className="wx-history-empty";tbody.append(tr);return;
   }
   for(const x of rows){
     const tr=document.createElement("tr");
     td(tr,localTime(x.market_close));
     const cell=document.createElement("td");
     const name=document.createElement("span");
     name.className="wx-history-city";
     name.textContent=cityLabels[x.city]||safeString(x.city);
     const ticker=document.createElement("span");
     ticker.className="wx-history-ticker";
     ticker.textContent=safeString(x.market_ticker);
     cell.append(name,ticker);tr.append(cell);
     badge(tr,x.forecast);
     td(tr,percent(x.directional_probability));
     td(tr,percent(x.confidence));
     let reason="Official final result not checked";
     let label="NOT CHECKED";
     let kind="PENDING";
     if(x.pending_reason==="HOUR_NOT_FINISHED"){
       reason="Forecast hour has not finished";label="UPCOMING";
     }else if(x.settlement_check_state==="AWAITING_FINALIZATION"){
       reason="Kalshi has not finalized this market";label="NOT FINAL";
       kind="AWAITING_FINALIZATION";
     }else if(x.settlement_check_state==="FETCH_ERROR"){
       reason="Official market API request failed";label="FEED ERROR";
       kind="FETCH_ERROR";
     }else if(x.settlement_check_state==="INVALID_OFFICIAL_EVIDENCE"){
       reason="Official response needs manual verification";label="VERIFY ERROR";
       kind="INVALID_OFFICIAL_EVIDENCE";
     }else if(x.settlement_check_state==="VERIFIED"){
       reason="Final evidence present; refresh or inspect history audit";
       label="RECHECK";
     }
     const outcome=td(tr,x.actual_side||reason);
     if(x.verdict==="PENDING"&&x.settlement_check_reason)
       outcome.title=x.settlement_check_reason;
     badge(tr,x.verdict==="PENDING"?kind:x.verdict,
           x.verdict==="PENDING"?label:null);
     tbody.append(tr);
   }
 }
 async function refresh(){
   if(refreshing)return;refreshing=true;
   try{
     const response=await fetch("/api/weather/hourly/history",{cache:"no-store"});
     if(!response.ok)throw new Error("HTTP "+response.status);
     const next=await response.json();
     if(next.mode!=="HOURLY_HISTORY_READ_ONLY"||
        next.real_orders_enabled!==false||!next.summary||
        !Array.isArray(next.forecasts))throw new Error("Unexpected weather history response");
     history=next;render();
   }catch(err){
     set("wx-history-status","Weather history unavailable · "+err.message);
     if(!history&&tbody){
       tbody.replaceChildren();const tr=document.createElement("tr");
       const c=td(tr,"Weather history feed unavailable. No results can be verified.");
       c.colSpan=7;c.className="wx-history-empty";tbody.append(tr);
     }
   }finally{refreshing=false}
 }
 if(filter)filter.addEventListener("change",render);
 refresh();setInterval(refresh,20000);
})();
</script>"""
