from __future__ import annotations

"""Self-contained, responsive KalshiEdge dashboard. All data stays on localhost."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b1421">
<title>KalshiEdge — Live Forecast &amp; History</title>
<style>
:root {
  color-scheme:dark;--bg:#09111e;--panel:#142131;--panel2:#172638;--line:#2a3d52;
  --text:#e6eff6;--muted:#94a8b9;--mint:#8ee5c0;--pink:#ff99ad;--cyan:#65d9e8;
  --amber:#f4c583;--radius:16px;
  font-family:"Segoe UI",Inter,system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(1100px 650px at 100% -180px,#13374b 0%,transparent 65%),var(--bg);color:var(--text)}
button,select{font:inherit}
button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--cyan);outline-offset:3px}
a{color:var(--cyan)}
.shell{max-width:1340px;margin:0 auto;padding:0 28px 72px}
header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:25px 0;border-bottom:1px solid var(--line)}
.identity{display:flex;align-items:center;gap:12px}
.mark{display:grid;place-items:center;border-radius:12px;background:linear-gradient(145deg,#54cfdd,#92e4c1);color:#102b35;width:42px;height:42px;font-size:23px;font-weight:900}
.name{font-size:19px;font-weight:800;letter-spacing:.8px}.tag{font-size:10px;font-weight:700;letter-spacing:1.8px;color:var(--muted);margin-top:4px}
.header-right{display:flex;align-items:center;gap:15px;color:var(--muted);font-size:12px}
.status-indicator{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--amber);margin-right:6px;box-shadow:0 0 10px #e6ba6b44}
.status-indicator.on{background:var(--mint);box-shadow:0 0 12px #8ee5c077}
.section-intro{margin:29px 0 20px}
.eyebrow{font-size:11px;font-weight:700;letter-spacing:2.4px;color:var(--cyan)}
h1{font-size:clamp(29px,4vw,42px);font-weight:700;letter-spacing:-1.6px;margin:8px 0}
.subtitle{font-size:13px;color:var(--muted);margin:0;line-height:1.6}
.layout{display:grid;grid-template-columns:minmax(325px,445px) minmax(0,1fr);align-items:stretch;gap:18px}
.card{background:linear-gradient(160deg,#172634,#111c2b);border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 20px 50px #00000018}
.forecast{position:relative;padding:25px 26px 23px;border-top:2px solid var(--cyan);min-height:535px}
.panel-label{color:#a9c2d2;font-size:10px;font-weight:800;letter-spacing:2px}
.lock-banner{margin:17px 0 22px;border:1px solid #876d43;border-left:4px solid #d4ab64;background:#332b23;border-radius:6px;padding:15px 15px 13px}
.lock-banner.locked{border-color:#417988;border-left-color:var(--cyan);background:#12323c}
.lock-banner.pending{border-color:#6b6a65;border-left-color:#9e9b89;background:#2a2930}
.lock-heading{font-size:12px;font-weight:800;letter-spacing:.5px;color:var(--amber)}
.lock-banner.locked .lock-heading{color:var(--cyan)}
.lock-banner.pending .lock-heading{color:#c5c2b7}
.lock-desc{font-size:12px;line-height:1.5;color:#d4dbe0;margin-top:8px}
.direction{font-size:clamp(62px,8vw,83px);line-height:1.05;font-weight:300;letter-spacing:-3px;margin:12px 0 12px}
.direction.up{color:var(--mint)}.direction.down{color:var(--pink)}.direction.skip{color:var(--amber)}
.direction.waiting{font-size:42px;letter-spacing:-1px;color:#9bafc3;margin:22px 0}
.direction-caption{font-size:12px;line-height:1.6;color:var(--muted);min-height:24px}
.confidence-row{margin-top:27px;display:flex;gap:13px;align-items:center}
.confidence-value{font-size:38px;line-height:1;font-weight:300;letter-spacing:-1.1px;font-variant-numeric:tabular-nums}
.confidence-label{font-size:12px;color:var(--text)}
.confidence-sub{font-size:10px;color:var(--muted);margin-top:5px}
.progress{height:4px;background:#304558;border-radius:5px;overflow:hidden;margin:14px 0 15px}
.progress>div{width:0;height:100%;background:var(--cyan);transition:width .3s}
.note{min-height:42px;font-size:12px;line-height:1.65;color:#b2c3d1}
.two-times{display:flex;justify-content:space-between;gap:12px;border-top:1px solid var(--line);margin-top:24px;padding-top:18px}
.time-label{font-size:11px;color:var(--muted);margin-bottom:7px}
.time-value{font-size:19px;font-variant-numeric:tabular-nums;letter-spacing:.25px}
.right{text-align:right}
.record-note{border-left:1px solid #48687b;padding-left:13px;margin-top:25px}
.record-title{font-size:12px;color:#c8dbe6}
.record-details{font-size:11px;line-height:1.7;color:var(--muted);margin-top:9px}
details{margin-top:15px;font-size:11px;color:var(--muted)}
summary{cursor:pointer;user-select:none}
.detail-grid{display:grid;grid-template-columns:1fr auto;column-gap:9px;row-gap:8px;padding-top:14px;word-break:break-word}
.detail-grid strong{color:var(--text);font-weight:500;text-align:right}
.context{display:flex;flex-direction:column;gap:18px}
.context-header{padding:24px 25px 21px}
.context-title{display:flex;align-items:center;justify-content:space-between;gap:10px}
.context-title h2{font-size:18px;margin:0;letter-spacing:-.35px}
.chip{font-size:10px;color:var(--cyan);border:1px solid #28576c;background:#12313d;border-radius:30px;padding:6px 10px;white-space:nowrap}
.market-ticker{font-size:12px;color:var(--muted);margin-top:11px;overflow-wrap:anywhere}
.market-data{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px;margin-top:24px}
.datum{background:#0f1c2b;border:1px solid #24364a;border-radius:11px;padding:19px}
.datum-label{font-size:11px;color:var(--muted)}
.datum-number{font-size:clamp(18px,2.6vw,25px);font-variant-numeric:tabular-nums;letter-spacing:-.4px;margin:12px 0 0;word-break:break-all}
.datum-hint{font-size:11px;color:var(--muted);margin-top:9px}
.healthy{color:var(--mint)}.unhealthy{color:var(--amber)}.pink{color:var(--pink)}
.health{padding:21px 25px;flex:1}
.health h3{font-size:14px;margin:0 0 17px}
.health-row{display:flex;gap:10px;justify-content:space-between;border-bottom:1px solid #293a4c;padding:11px 0;font-size:12px;color:var(--muted)}
.health-row:last-child{border-bottom:0}.health-row strong{color:var(--text);font-weight:600;overflow-wrap:anywhere;text-align:right}
.history-header{display:flex;align-items:end;justify-content:space-between;flex-wrap:wrap;gap:12px;margin:40px 0 16px}
.history-header h2{margin:7px 0;font-size:24px;letter-spacing:-.7px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.kpi{padding:19px 18px}.kpi-label{font-size:12px;color:var(--muted)}
.kpi-num{font-size:31px;font-weight:650;letter-spacing:-.7px;font-variant-numeric:tabular-nums;margin:11px 0 5px}
.kpi-caption{font-size:11px;line-height:1.55;color:#899eb0}
.history-section{margin-top:20px;overflow:hidden}
.table-controls{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;padding:20px;border-bottom:1px solid var(--line)}
.table-controls h3{margin:0;font-size:15px}
.filter{border:1px solid #355069;color:var(--text);background:#172739;padding:9px 12px;border-radius:7px;font-size:12px}
.table-wrap{width:100%;overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:870px;font-size:12px}
th{background:#19293a;color:#aabaca;font-size:10px;text-transform:uppercase;letter-spacing:1px;text-align:left;padding:14px 18px;white-space:nowrap}
td{color:#dde9f2;padding:15px 18px;border-top:1px solid #26394b;white-space:nowrap;font-variant-numeric:tabular-nums}
tr:hover td{background:#192c3b}
.pill{display:inline-block;border-radius:6px;border:1px solid #536578;padding:4px 9px;font-size:10px;font-weight:800;letter-spacing:.6px}
.pill.ABOVE,.pill.CORRECT{color:var(--mint);border-color:#396a62;background:#15322f}
.pill.BELOW,.pill.INCORRECT{color:var(--pink);border-color:#794759;background:#33222c}
.pill.SKIP{color:var(--amber);border-color:#74603c;background:#32291f}
.pill.PENDING{color:#b3cde5;border-color:#435976;background:#202c43}
.empty{padding:26px;color:var(--muted);text-align:center}
footer{font-size:11px;line-height:1.8;color:#849baf;margin-top:24px}
.training-card{padding:25px 26px;margin-top:26px;border-top:2px solid #56d6c1}
.training-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.training-head h2{font-size:24px;letter-spacing:-.7px;margin:7px 0 5px}
.training-pill{border:1px solid #3b7180;background:#14323a;color:var(--mint);padding:7px 12px;border-radius:25px;font-size:11px;font-weight:700;letter-spacing:.7px}
.training-pill.waiting{border-color:#6b6049;color:#efd09a;background:#2a261f}
.training-cycle{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:24px 0}
.training-step{padding:14px;border-radius:10px;background:#101e2b;border:1px solid #2a3c51}
.training-step.active{border-color:var(--cyan);background:#143543}
.training-step.completed{border-color:#337266}
.training-step small{display:block;font-size:10px;color:var(--muted);letter-spacing:1.1px}
.training-step strong{display:block;margin-top:8px;font-size:12px}
.training-layout{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:24px;align-items:start}
.training-progress-title{display:flex;justify-content:space-between;gap:12px;align-items:center;font-size:12px;font-weight:600}
.training-big-percent{font-size:26px;font-variant-numeric:tabular-nums;color:var(--mint)}
.training-bar{height:9px;background:#2b3e51;border-radius:12px;overflow:hidden;margin:13px 0 12px}
.training-bar>div{height:100%;width:0%;background:linear-gradient(90deg,#52b8d4,#8fe7c3);border-radius:12px;transition:width .4s}
.training-detail{font-size:12px;color:var(--muted);line-height:1.65;min-height:38px}
.training-counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:18px}
.training-count{border:1px solid #2b4154;background:#10202f;padding:11px;border-radius:8px}
.training-count b{display:block;font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
.training-count span{display:block;color:var(--muted);font-size:10px;line-height:1.4;margin-top:5px}
.training-action{border-left:1px solid var(--line);padding-left:23px}
.training-action h3{font-size:14px;margin:0 0 9px}
.training-action p{font-size:12px;color:var(--muted);line-height:1.65;margin:0 0 16px}
.train-button{width:100%;padding:14px 12px;border:0;border-radius:9px;background:linear-gradient(130deg,#7ae2ca,#50bfd4);color:#092632;font-weight:800;font-size:13px;cursor:pointer}
.train-button:hover:not(:disabled){filter:brightness(1.12)}
.train-button:disabled{background:#344859;color:#9daab7;cursor:not-allowed}
.train-feedback{font-size:11px;color:#acc9d6;line-height:1.6;margin-top:12px;overflow-wrap:anywhere;min-height:18px}
@media(max-width:800px){.training-layout{grid-template-columns:1fr}.training-action{border-left:0;border-top:1px solid var(--line);padding:20px 0 0}.training-cycle{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:960px){.layout{grid-template-columns:1fr}.context{display:grid;grid-template-columns:1.5fr 1fr}.forecast{min-height:0}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px){.shell{padding:0 14px 40px}header{padding:15px 0}.tag{font-size:8px}.header-right{font-size:10px}.section-intro{margin:22px 0 16px}.forecast{padding:20px}.context{display:flex}.context-header,.health{padding:20px}.market-data{gap:9px}.datum{padding:14px}.datum-number{font-size:19px}.history-header{margin-top:28px}.kpi{padding:14px}.kpi-num{font-size:27px}th,td{padding:11px}}
</style>
</head>
<body>
<div class="shell">
<header>
 <div class="identity"><div class="mark" aria-hidden="true">K</div><div><div class="name">KALSHIEDGE</div><div class="tag">BTC · 15-MINUTE RESEARCH</div></div></div>
 <div class="header-right"><span><span class="status-indicator" id="connection-dot"></span><span id="connection-label">Connecting</span></span><span class="chip">READ-ONLY</span></div>
</header>
<div class="section-intro"><div class="eyebrow">LIVE PREDICTION ENGINE</div><h1>Bitcoin market outlook</h1>
<p class="subtitle">Live model estimates, one immutable final forecast per market, and settlement-verified history. No automatic trades.</p></div>
<section class="layout" aria-label="Current round">
 <article class="card forecast">
  <div class="panel-label">THIS ROUND'S FORECAST</div>
  <div class="lock-banner pending" id="lock-banner"><div class="lock-heading" id="lock-heading">◌ &nbsp; WAITING FOR MARKET</div><div class="lock-desc" id="lock-description">Connecting to a currently open Kalshi BTC market.</div></div>
  <div class="direction waiting" id="direction">WAITING</div>
  <div class="direction-caption" id="direction-caption">No current model call available.</div>
  <div class="confidence-row"><div class="confidence-value" id="confidence">—</div><div><div class="confidence-label">Model confidence</div><div class="confidence-sub" id="confidence-sub">Directional strength, not measured accuracy</div></div></div>
  <div class="progress" aria-label="Confidence indicator"><div id="confidence-fill"></div></div>
  <div class="note" id="confidence-note">A final forecast requires at least 69% model confidence at the four-minute checkpoint.</div>
  <div class="two-times">
   <div><div class="time-label">Round closes in</div><div class="time-value" id="round-countdown">—</div></div>
   <div class="right"><div class="time-label" id="lock-time-label">Forecast locks at</div><div class="time-value" id="lock-time">—</div></div>
  </div>
  <div class="record-note"><div class="record-title" id="record-title">Waiting to save the forecast</div>
   <div class="record-details" id="record-description">At the scheduled checkpoint, ABOVE/BELOW is locked only if model inputs are healthy and confidence meets 69%. Otherwise the decision is SKIP.</div>
  </div>
  <details id="record-details"><summary>Forecast record details</summary><div class="detail-grid">
   <span>Model</span><strong id="detail-model">—</strong>
   <span>Model probability</span><strong id="detail-probability">—</strong>
   <span>Model hash</span><strong id="detail-hash">—</strong>
   <span>Record status</span><strong id="detail-status">—</strong>
   <span>Reason</span><strong id="detail-reason">—</strong>
  </div></details>
 </article>
 <div class="context">
  <article class="card context-header">
   <div class="context-title"><h2>Market snapshot</h2><span class="chip" id="market-state">SEARCHING</span></div>
   <div class="market-ticker" id="market-ticker">Waiting for active ticker</div>
   <div class="market-data">
    <div class="datum"><div class="datum-label">Price to beat</div><div class="datum-number" id="strike">—</div><div class="datum-hint">Published Kalshi strike</div></div>
    <div class="datum"><div class="datum-label">BTC reference</div><div class="datum-number" id="btc">—</div><div class="datum-hint">Last live Binance bar</div></div>
    <div class="datum"><div class="datum-label">Model P(ABOVE)</div><div class="datum-number" id="p-up">—</div><div class="datum-hint">Current or saved probability</div></div>
    <div class="datum"><div class="datum-label">Kalshi implied P(ABOVE)</div><div class="datum-number" id="prior">—</div><div class="datum-hint">Current quoted benchmark</div></div>
    <div class="datum"><div class="datum-label">Expected BTC move (±1σ)</div><div class="datum-number" id="vol-expected">—</div><div class="datum-hint">Volatility model · before settlement</div></div>
    <div class="datum"><div class="datum-label">Volatility probability weight</div><div class="datum-number" id="vol-weight">—</div><div class="datum-hint">Shown only for promoted upgraded models</div></div>
   </div>
  </article>
  <article class="card health">
   <h3>Prediction status</h3>
   <div class="health-row"><span>Data feed</span><strong id="feed-status">Awaiting data</strong></div>
   <div class="health-row"><span>Model version</span><strong id="model-version">—</strong></div>
   <div class="health-row"><span>Checkpoint rule</span><strong>240 seconds before close</strong></div>
   <div class="health-row"><span>SKIP threshold</span><strong>69% confidence</strong></div>
   <div class="health-row"><span>Latest prediction</span><strong id="latest-at">—</strong></div>
   <div class="health-row"><span>Market closes</span><strong id="close-at">—</strong></div>
  </article>
 </div>
</section>
<section class="card training-card" aria-label="Adaptive model training">
 <div class="training-head">
  <div><div class="eyebrow">ADAPTIVE LEARNING / MANUAL TRAINING</div><h2>Learning cycle</h2>
   <p class="subtitle">Three mistake rounds, then one correct-market refresher. Evaluation controls promotion.</p>
  </div>
  <span class="training-pill waiting" id="train-status-pill">COLLECTING DATA</span>
 </div>
 <div class="training-cycle" id="train-steps">
  <div class="training-step active" id="train-step-1"><small>ROUND 1</small><strong>Mistakes</strong></div>
  <div class="training-step" id="train-step-2"><small>ROUND 2</small><strong>Mistakes</strong></div>
  <div class="training-step" id="train-step-3"><small>ROUND 3</small><strong>Mistakes</strong></div>
  <div class="training-step" id="train-step-4"><small>ROUND 4</small><strong>Correct refresher</strong></div>
 </div>
 <div class="training-layout">
  <div>
   <div class="training-progress-title"><span id="training-target">Round 1 · Learn from mistakes</span><strong class="training-big-percent" id="training-progress-pct">0%</strong></div>
   <div class="training-bar" role="progressbar" aria-label="Training data collection readiness" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="training-bar"><div id="training-bar-fill"></div></div>
   <div class="training-detail" id="training-reason">Checking settled predictions, verified feature rows and independent evaluation markets.</div>
   <div class="training-counts">
    <div class="training-count"><b id="training-cases">—</b><span>Eligible markets / 12</span></div>
    <div class="training-count"><b id="training-outcomes">—</b><span>Outcome classes / 2</span></div>
    <div class="training-count"><b id="training-lockbox">—</b><span>New markets / 20</span></div>
   </div>
  </div>
  <div class="training-action">
   <h3 id="training-headline">Collecting training data</h3>
   <p id="training-description">Training remains disabled until verified examples, two settlement classes and a new untouched lockbox are ready.</p>
   <button class="train-button" type="button" disabled id="train-now">Train Now</button>
   <div class="train-feedback" id="train-feedback" role="status" aria-live="polite"></div>
  </div>
 </div>
</section>
<section aria-label="Final Forecast history">
 <div class="history-header"><div><div class="eyebrow">VERIFIED PERFORMANCE</div><h2>Final forecast history</h2><p class="subtitle">Official Kalshi settlements only · Accuracy = correct ÷ (correct + incorrect)</p></div><span id="history-status" class="subtitle">Loading history…</span></div>
 <div class="kpis">
  <div class="card kpi"><div class="kpi-label">Settled forecast accuracy</div><div class="kpi-num healthy" id="accuracy">—</div><div class="kpi-caption" id="accuracy-detail">Awaiting settled directions</div></div>
  <div class="card kpi"><div class="kpi-label">Correct / Incorrect</div><div class="kpi-num" id="correctness">0 / 0</div><div class="kpi-caption">Only settled ABOVE/BELOW</div></div>
  <div class="card kpi"><div class="kpi-label">SKIP decisions</div><div class="kpi-num" id="skips">0</div><div class="kpi-caption">Excluded from accuracy</div></div>
  <div class="card kpi"><div class="kpi-label">Pending settlement</div><div class="kpi-num" id="pending">0</div><div class="kpi-caption">Never counted as incorrect</div></div>
 </div>
 <div class="card history-section">
  <div class="table-controls"><h3>Recorded final forecasts</h3><label class="subtitle">Show &nbsp;<select class="filter" id="filter"><option value="ALL">All forecasts</option><option value="CORRECT">Correct</option><option value="INCORRECT">Incorrect</option><option value="SKIP">SKIP</option><option value="PENDING">Pending</option></select></label></div>
  <div class="table-wrap"><table><thead><tr><th>Market closes (local)</th><th>Market</th><th>Forecast</th><th>Probability</th><th>Confidence</th><th>Settled outcome</th><th>Result</th></tr></thead><tbody id="history-rows"><tr><td colspan="7" class="empty">Awaiting history data…</td></tr></tbody></table></div>
 </div>
</section>
<footer>Read-only research tool. Model confidence is a directional-strength score and is not the same as measured forecast accuracy. At the 69% confidence gate, directional probability is at least approximately 84.5% by the model's current definition; this is not a guarantee of correctness. History excludes pending and SKIP forecasts from directional accuracy.</footer>
</div>
<script>
"use strict";
const el=id=>document.getElementById(id);
const text=(id,value)=>{el(id).textContent=value==null?"—":String(value)};
const pct=n=>Number.isFinite(n)?(n*100).toFixed(2)+"%":"—";
const money=n=>Number.isFinite(n)?"$"+n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):"—";
const local=ms=>Number.isFinite(ms)?new Date(ms).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"}):"—";
const pill=s=>{const span=document.createElement("span");span.className="pill "+(String(s).match(/^(ABOVE|BELOW|SKIP|CORRECT|INCORRECT|PENDING)$/)?.[0]||"");span.textContent=s;return span};
let live=null,history=null;
function countdown(ms){if(!Number.isFinite(ms))return "—";let sec=Math.max(0,Math.floor((ms-Date.now())/1000));return String(Math.floor(sec/60)).padStart(2,"0")+":"+String(sec%60).padStart(2,"0")}
function clock(){if(!live||!live.market_close_ts_ns)return;text("round-countdown",countdown(live.market_close_ts_ns/1e6))}
function renderLive(data){
 live=data;const p=data.latest_prediction,f=data.final_forecast;
 const current=!!data.market_ticker,locked=!!f,healthy=data.status==="LIVE";
 el("connection-dot").classList.toggle("on",healthy);
 text("connection-label",locked?"Forecast locked":healthy?"Live feeds":"Waiting for feeds");
 text("market-state",current?(healthy?"LIVE":data.status):"SEARCHING");
 text("market-ticker",data.market_ticker||"Waiting for active ticker");
 text("strike",money(data.strike));
 text("btc",money(p?.btc_reference));
 text("prior",pct(p?.kalshi_prior));
 text("vol-expected",Number.isFinite(p?.volatility_expected_move_pct)?"±"+p.volatility_expected_move_pct.toFixed(3)+"%":"—");
 text("vol-weight",Number.isFinite(p?.volatility_weight)?Math.round(p.volatility_weight*100)+"%":"—");
 text("p-up",pct(locked?f.p_yes:p?.p_yes));
 text("model-version",f?.model_version||p?.model_version||"—");
 text("latest-at",p?.prediction_ts_ns?local(p.prediction_ts_ns/1e6):"—");
 text("close-at",data.market_close_ts_ns?local(data.market_close_ts_ns/1e6):"—");
 text("feed-status",data.status==="LIVE"?"HEALTHY":data.status==="DEGRADED"?"INPUTS NOT HEALTHY":data.status==="WAITING_FOR_PREDICTOR"?"PREDICTOR NOT RUNNING":"WAITING FOR MARKET");
 if(data.market_close_ts_ns){
   text("lock-time",local(data.checkpoint_ts_ns/1e6));
   text("lock-time-label",locked?"Locked at":"Forecast locks at");
 }else{text("lock-time","—");text("lock-time-label","Forecast locks at")}
 clock();
 const banner=el("lock-banner");
 banner.className="lock-banner "+(locked?"locked":current&&data.checkpoint_ts_ns/1e6<=Date.now()?"pending":"");
 if(locked){text("lock-heading","▣  LOCKED · FINAL FORECAST");text("lock-description","This forecast is saved and will not change.");
 }else if(current&&data.checkpoint_ts_ns/1e6>Date.now()){
   text("lock-heading","♧  UNLOCKED · LIVE PREVIEW");text("lock-description","This call can change until it locks at "+local(data.checkpoint_ts_ns/1e6)+".");
 }else if(current){text("lock-heading","◌  AWAITING FINAL RECORD");text("lock-description","The checkpoint has passed. Only a saved final record counts.");
 }else{text("lock-heading","◌  WAITING FOR MARKET");text("lock-description","Awaiting current Kalshi BTC market discovery and fresh predictions.")}
 const side=locked?f.forecast:p?.status==="OK"?p.predicted_side:null;
 const display=side==="ABOVE"?"UP":side==="BELOW"?"DOWN":side==="SKIP"?"SKIP":current?"WAITING":"WAITING";
 const direction=el("direction");direction.className="direction "+(display==="UP"?"up":display==="DOWN"?"down":display==="SKIP"?"skip":"waiting");direction.textContent=display;
 text("direction-caption",locked?"Recorded before the outcome. This forecast stays fixed.":p?.status==="OK"?"Live preview · requires at least 69% confidence at lock.":current?"Current forecast unavailable: "+(p?.reason||"waiting for healthy feeds"):"No currently readable market and prediction.");
 const strength=locked?f.confidence:p?.confidence;
 text("confidence",Number.isFinite(strength)?Math.round(strength*100)+"%":"—");
 el("confidence-fill").style.width=Number.isFinite(strength)?Math.min(100,Math.max(0,strength*100))+"%":"0%";
 text("confidence-sub",locked?"Locked with this forecast":"Live strength · can change");
 text("confidence-note",locked?"Saved "+display+" call has model probability "+pct(f.directional_probability)+". Model confidence is not historical accuracy.":"Model confidence measures the strength of the estimated direction. At 240 seconds before close, values below 69% produce SKIP.");
 text("record-title",locked?"Forecast saved":"Waiting to save the forecast");
 text("record-description",locked?"The final decision is immutable. Official settlement verification will score ABOVE or BELOW; SKIP is excluded from accuracy.":"The model locks once at the scheduled 240-second checkpoint. Low-confidence, late, or unhealthy inputs produce SKIP.");
 text("detail-model",f?.model_version||p?.model_version);
 text("detail-probability",pct(locked?f.directional_probability:p?.predicted_side==="ABOVE"?p.p_yes:p?.predicted_side==="BELOW"?p.p_no:null));
 text("detail-hash",f?.model_hash||p?.model_hash||"—");
 text("detail-status",locked?"IMMUTABLE":p?.status||"WAITING");
 text("detail-reason",locked?f.reason||"OK":p?.reason||"—");
}
function cell(tr,value){const td=document.createElement("td");if(value instanceof Node)td.appendChild(value);else td.textContent=value==null?"—":String(value);tr.appendChild(td)}
function renderHistory(data){
 history=data;const s=data.summary;
 text("accuracy",pct(s.accuracy));
 text("accuracy-detail",s.settled_directional_forecasts+" settled directional predictions");
 text("correctness",s.correct+" / "+s.incorrect);
 text("skips",s.skipped);
 text("pending",s.pending_settlement);
 text("history-status",s.total_final_forecasts+" records · Refreshed "+new Date().toLocaleTimeString());
 const filter=el("filter").value,body=el("history-rows");body.replaceChildren();
 const rows=data.forecasts.filter(f=>filter==="ALL"||f.verdict===filter);
 if(!rows.length){const tr=document.createElement("tr");const td=document.createElement("td");td.colSpan=7;td.className="empty";td.textContent="No matching final forecasts yet.";tr.appendChild(td);body.appendChild(tr);return}
 for(const f of rows){
   const tr=document.createElement("tr");
   cell(tr,new Date(f.market_close).toLocaleString());
   cell(tr,f.market_ticker);
   cell(tr,pill(f.forecast));
   cell(tr,pct(f.directional_probability));
   cell(tr,pct(f.confidence));
   cell(tr,f.actual_side||"Awaiting settlement");
   cell(tr,pill(f.verdict));
   body.appendChild(tr);
 }
}
async function refreshLive(){try{const response=await fetch("/api/live",{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);renderLive(await response.json())}
catch(err){el("connection-dot").classList.remove("on");text("connection-label","Dashboard disconnected");text("feed-status","DASHBOARD ERROR");console.warn(err)}}
async function refreshHistory(){try{const response=await fetch("/api/history",{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);renderHistory(await response.json())}
catch(err){text("history-status","Could not refresh: "+err.message)}}
el("filter").addEventListener("change",()=>{if(history)renderHistory(history)});
let trainingStatus=null;
function renderTraining(info){
 trainingStatus=info;
 const run=info.run||{};
 const running=!!info.running;
 const step=Number(info.cycle_position)||1;
 const phase=String(run.stage||"");
 const pctValue=Math.max(0,Math.min(100,Number(info.progress_percent)||0));
 const state=running?(phase==="EVALUATING"?"EVALUATING":"TRAINING"):info.ready?"TRAINING READY":info.status==="BLOCKED"?"DATA BLOCKED":info.status==="EVALUATION_PENDING"?"EVALUATION PENDING":"COLLECTING DATA";
 const pillEl=el("train-status-pill");pillEl.textContent=state;pillEl.className="training-pill "+(running||info.ready?"":"waiting");
 text("training-target","Round "+info.round_number+" · "+info.mode_label);
 text("training-progress-pct",pctValue+"%");
 el("training-bar-fill").style.width=pctValue+"%";
 el("training-bar").setAttribute("aria-valuenow",String(pctValue));
 text("training-cases",info.eligible_markets+" / "+info.target_markets);
 text("training-outcomes",info.outcome_classes+" / 2");
 text("training-lockbox",info.new_evaluation_markets+" / "+info.target_new_markets);
 text("training-reason",info.reason);
 for(let i=1;i<=4;i++){
   const stepEl=el("train-step-"+i);
   stepEl.className="training-step "+(i===step?"active":i<step?"completed":"");
 }
 text("training-headline",running?"Training in progress":info.ready?"Training Ready":"Collecting training data");
 text("training-description",running?
   "Running "+info.mode_label.toLowerCase()+" training. The fresh lockbox is evaluated once after training; only a passing candidate can replace the promoted model. Keep KalshiEdge open.":
   info.ready?"All prerequisites passed. Train Now runs this round, evaluates the reserved lockbox once, and advances the 3:1 cycle whether promotion passes or fails.":
   "Training stays disabled until the required eligible markets, both settlement classes, and fresh evaluation markets are present.");
 el("train-now").disabled=!info.ready||running||!!run.stage&&run.stage==="STARTING";
 if(running){text("train-feedback","Worker: "+phase+" · Round "+info.round_number)}
 else if(phase==="COMPLETE"){text("train-feedback","Previous round finished · "+(run.promoted?"model promoted (restart KalshiEdge to load it).":"promotion criteria not met; existing model retained."))}
 else if(phase==="FAILED"||phase==="INTERRUPTED"){text("train-feedback","Previous run "+phase.toLowerCase()+": "+(run.error||"see local logs"))}
 else{text("train-feedback",info.ready?"Ready for manual training.":"The progress bar measures data readiness, not training time.")}
}
async function refreshTraining(){
 try{
   const response=await fetch("/api/training",{cache:"no-store"});
   if(!response.ok)throw Error("HTTP "+response.status);
   renderTraining(await response.json());
 }catch(err){text("training-reason","Cannot verify training readiness: "+err.message);el("train-now").disabled=true;}
}
el("train-now").addEventListener("click",async()=>{
 if(!trainingStatus?.ready||trainingStatus.running)return;
 if(!confirm("Start round "+trainingStatus.round_number+" ("+trainingStatus.mode_label+")? This can run for hours. After fitting, the fresh untouched lockbox will be evaluated exactly once and a model may be promoted only if it passes the benchmark and incumbent checks."))return;
 const button=el("train-now");button.disabled=true;text("train-feedback","Submitting training request…");
 try{
   const response=await fetch("/api/training/start",{
     method:"POST",headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"train"},
     body:"{}",cache:"no-store"
   });
   const result=await response.json();
   if(!response.ok)throw Error(result.error||"HTTP "+response.status);
   text("train-feedback","Training round "+result.round_number+" started · "+result.mode+" · see local worker log for diagnostics.");
 }catch(err){text("train-feedback","Could not start training: "+err.message)}
 await refreshTraining();
});
refreshLive();refreshHistory();refreshTraining();
setInterval(refreshLive,1500);setInterval(refreshHistory,5000);setInterval(refreshTraining,5000);setInterval(clock,250);
</script>
</body></html>
"""
