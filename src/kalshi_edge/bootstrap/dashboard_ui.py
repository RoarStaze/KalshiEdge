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
/* Position-only header: actual holdings, never archived forecast status. */
.forecast .lock-banner.position-up{background:#102e29;border-color:#267b66;border-left-color:#36dda9}
.forecast .lock-banner.position-down{background:#322329;border-color:#9b5263;border-left-color:#ff8197}
.forecast .lock-banner.position-flat{background:#1b2629;border-color:#3b5356;border-left-color:#91aaa7}
.forecast .lock-banner .lock-heading{font-weight:850}
.forecast .lock-banner.position-up .lock-heading{color:#62edbc}
.forecast .lock-banner.position-down .lock-heading{color:#ff91a3}
.forecast .lock-banner .lock-desc.pnl-gain{color:#53e6b2;font-weight:750;font-variant-numeric:tabular-nums}
.forecast .lock-banner .lock-desc.pnl-loss{color:#ff7d93;font-weight:750;font-variant-numeric:tabular-nums}
.forecast .lock-banner .lock-desc.pnl-pending{color:#a6bdba;font-weight:650}

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
.training-layout{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:24px;align-items:start}
.training-progress-title{display:flex;justify-content:space-between;gap:12px;align-items:center;font-size:12px;font-weight:600}
.training-big-percent{font-size:26px;font-variant-numeric:tabular-nums;color:var(--mint)}
.training-bar{height:9px;background:#2b3e51;border-radius:12px;overflow:hidden;margin:13px 0 12px}
.training-bar>div{height:100%;width:0%;background:linear-gradient(90deg,#52b8d4,#8fe7c3);border-radius:12px;transition:width .4s}
.training-detail{font-size:12px;color:var(--muted);line-height:1.65;min-height:38px}
.training-action{border-left:1px solid var(--line);padding-left:23px}
.training-forward{padding:22px 24px;margin-top:22px;background:linear-gradient(135deg,rgba(30,65,61,.67),rgba(18,34,39,.9));border:1px solid #2e6257;border-radius:14px}
.training-forward .training-bar{margin:13px 0 10px}
.training-launch{display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;padding-top:13px;margin-top:16px;border-top:1px solid #2d514c}
.training-launch h3{font-size:17px;margin:0 0 4px}
.training-launch p{margin:0;color:var(--muted);font-size:12px;line-height:1.6}
.training-launch .train-button{min-width:190px;padding:13px 22px;background:#66cdb2;border-radius:10px;color:#10201d;border:none;box-shadow:0 6px 22px rgba(78,196,158,.12)}
.training-launch .train-button:disabled{background:#2f4855;color:#81929d;box-shadow:none}
.training-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.training-actions button{border-radius:9px;padding:13px 17px;font-size:12px;font-weight:750;cursor:pointer;transition:background .2s,border-color .2s}
.train-pause-button{background:#3f3425;border:1px solid #b68c4b;color:#f2cf95}
.train-resume-button{background:#1c493f;border:1px solid #4ab799;color:#b0f4db}
.training-actions button:disabled{opacity:.42;cursor:not-allowed}
.training-outcome.paused{background:#322f25;border-color:#8c7b4f;color:#f6e5bb}
.training-live-panel{background:linear-gradient(180deg,#132029,#111b23);border:1px solid #2c4350;border-radius:14px;margin-top:20px;padding:23px 24px}
.training-live-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.training-live-head h3{font-size:19px;margin:5px 0 0}
.training-worker-bar{height:10px;margin-top:16px}
.training-dataset-bar{height:7px;margin:8px 0 15px}
.training-dataset-bar[hidden]{display:none!important}
.training-stage-list{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;margin-top:19px}
.training-head-controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.training-model-identity{display:flex;flex-direction:column;gap:3px;border:1px solid #3e8275;background:#143931;border-radius:12px;padding:12px 17px;min-width:176px}
.training-model-identity span{font-size:10px;color:#93bfc0;letter-spacing:1.3px;font-weight:800}
.training-model-identity strong{font-size:25px;font-weight:750;color:#87efd0;letter-spacing:-.5px;font-variant-numeric:tabular-nums}
.training-model-identity small{font-size:10px;color:#afd0c6;line-height:1.5}
.training-stage{background:#172730;border:1px solid #304754;border-radius:9px;padding:12px 10px;min-height:70px;transition:background .25s,border-color .25s}
.training-stage .step-id{display:block;font-size:9px;letter-spacing:1px;color:#8da8aa}
.training-stage .step-name{font-size:12px;font-weight:700;margin-top:9px}
.training-stage.done{border-color:#2e7b66;background:#15372f}
.training-stage.current{border-color:#58d7be;background:#1e403b}
.training-stage.failed{border-color:#b97864;background:#3c2629}
.training-comparison{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:18px}
.training-comparison-note{grid-column:1/-1}
.training-metric{border:1px solid #304b57;background:#172731;border-radius:11px;padding:15px}
.training-metric h4{margin:0;color:#a5bbbe;font-size:12px;font-weight:600}
.training-metric strong{display:block;font-size:23px;margin-top:10px;color:#8ae4cc;font-variant-numeric:tabular-nums}
.training-metric small{color:#a3b9bb;font-size:11px;line-height:1.6}
.training-outcome{padding:16px 18px;border:1px solid #365260;background:#152731;border-radius:11px;margin-top:17px;font-size:13px;line-height:1.7;white-space:pre-wrap}
.training-audit{margin-top:15px;border:1px solid #304b57;background:#14232b;border-radius:11px;padding:17px 18px;font-size:12px;line-height:1.7;color:var(--text)}
.training-audit[hidden]{display:none!important}
.training-audit h4{font-size:14px;margin:0 0 9px;color:var(--mint)}
.training-audit-line{margin-top:5px;overflow-wrap:anywhere}
.training-audit-note{font-size:11px;color:var(--muted);margin-top:9px}
.training-outcome.pass{background:#14342c;border-color:#2a8970;color:#c2f3dd}
.training-outcome.fail{background:#352529;border-color:#96545a;color:#f6c3c6}
.training-outcome.pending{background:#353024;border-color:#9d844c;color:#f0ddac}
@media(max-width:800px){.training-stage-list{grid-template-columns:repeat(3,minmax(0,1fr))}.training-launch .train-button{width:100%}.training-actions{width:100%}.training-actions button{flex:1}}
@media(max-width:460px){.training-stage-list{grid-template-columns:repeat(2,minmax(0,1fr))}.training-comparison{grid-template-columns:1fr}}

.training-forward{margin-top:20px;padding:18px 20px;border:1px solid #31534d;background:#14231f;border-radius:12px}
.training-forward-head{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}
.training-forward-title{font-size:14px;font-weight:700;color:#92e2c8}
.training-forward-numbers{font-size:12px;color:var(--muted);margin-top:10px}
.training-forward-note{font-size:12px;color:var(--muted);line-height:1.6;margin:10px 0}
.training-forward-result{font-size:12px;line-height:1.6;margin-top:10px}
.training-forward button{margin-top:12px;padding:11px 16px;border-radius:8px;border:1px solid #5bc6a6;background:#216651;color:#fff;font-weight:700;cursor:pointer}
.training-forward button:disabled{opacity:.45;cursor:not-allowed}
.training-action h3{font-size:14px;margin:0 0 9px}
.training-action p{font-size:12px;color:var(--muted);line-height:1.65;margin:0 0 16px}
.train-button{width:100%;padding:14px 12px;border:0;border-radius:9px;background:linear-gradient(130deg,#7ae2ca,#50bfd4);color:#092632;font-weight:800;font-size:13px;cursor:pointer}
.train-button:hover:not(:disabled){filter:brightness(1.12)}
.train-button:disabled{background:#344859;color:#9daab7;cursor:not-allowed}
.train-feedback{font-size:11px;color:#acc9d6;line-height:1.6;margin-top:12px;overflow-wrap:anywhere;min-height:18px}
@media(max-width:800px){.training-layout{grid-template-columns:1fr}.training-action{border-left:0;border-top:1px solid var(--line);padding:20px 0 0}}
@media(max-width:960px){.layout{grid-template-columns:1fr}.context{display:grid;grid-template-columns:1.5fr 1fr}.forecast{min-height:0}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px){.shell{padding:0 14px 40px}header{padding:15px 0}.tag{font-size:8px}.header-right{font-size:10px}.section-intro{margin:22px 0 16px}.forecast{padding:20px}.context{display:flex}.context-header,.health{padding:20px}.market-data{gap:9px}.datum{padding:14px}.datum-number{font-size:19px}.history-header{margin-top:28px}.kpi{padding:14px}.kpi-num{font-size:27px}th,td{padding:11px}}

/* Continuous intramarket monitor, placed INSIDE the existing forecast card. */
.forecast .scalp-monitor{margin-top:15px;padding-top:14px;border-top:1px solid #34524d}
.forecast .scalp-monitor-title{font-size:10px;font-weight:850;letter-spacing:1.5px;color:#a6c7c1}
.forecast .scalp-monitor-row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.forecast .scalp-risk-value{font-size:12px;font-weight:900;letter-spacing:.8px;padding:6px 12px;border-radius:8px;border:1px solid #44545b;color:#b5c2c9;background:#17232a}
.forecast .scalp-risk-value.low{color:#58f1b4;border-color:#2c846a;background:#12372f}
.forecast .scalp-risk-value.medium{color:#ffd277;border-color:#9c7939;background:#393023}
.forecast .scalp-risk-value.high{color:#ff7f8b;border-color:#b8515f;background:#3d232c}
.forecast .scalp-monitor-note{font-size:10px;line-height:1.4;color:#a2b6b7;margin:7px 0 10px}
.forecast .scalp-flip-row{border:1px solid #39534f;border-radius:9px;background:rgba(15,34,34,.86);padding:9px 12px}
.forecast .scalp-flip-status{font-size:12px;font-weight:850;color:#a6c6bc;letter-spacing:.2px}
.forecast .scalp-flip-status.active{color:#ffd276}
.forecast .scalp-flip-status.down{color:#ff8196}
.forecast .scalp-flip-status.up{color:#55ecb4}
.forecast .scalp-monitor-disclaimer{font-size:9px;color:#849c9f;line-height:1.4;margin-top:9px}

</style>
</head>
<body>
<div class="shell">
<header>
 <div class="identity"><div class="mark" aria-hidden="true">K</div><div><div class="name">KALSHIEDGE</div><div class="tag">BTC · 15-MINUTE RESEARCH</div></div></div>
 <div class="header-right"><a href="http://127.0.0.1:8898/" title="Open local paper-only AutoTrader (start it separately)">AutoTrader (paper)</a><span><span class="status-indicator" id="connection-dot"></span><span id="connection-label">Connecting</span></span><span class="chip">READ-ONLY</span></div>
</header>
<div class="section-intro"><div class="eyebrow">LIVE PREDICTION ENGINE</div><h1>Bitcoin market outlook</h1>
<p class="subtitle">Live model estimates, one immutable final forecast per market, and settlement-verified history. No automatic trades.</p></div>
<section class="layout" aria-label="Current round">
 <article class="card forecast">
  <div class="panel-label">THIS ROUND'S FORECAST</div>
  <div class="lock-banner position-flat" id="lock-banner" aria-label="Actual held position and estimated unrealized profit or loss"><div class="lock-heading" id="lock-heading">POSITION UPDATING</div><div class="lock-desc pnl-pending" id="lock-description" role="status" aria-live="polite">UNREALIZED P/L —</div></div>
  <div class="direction waiting" id="direction">WAITING</div>
  <div class="direction-caption" id="direction-caption">No current model call available.</div>
  <div class="confidence-row"><div class="confidence-value" id="confidence">—</div><div><div class="confidence-label">Forecast probability</div><div class="confidence-sub" id="confidence-sub">Probability of the predicted direction · model estimate, not accuracy</div></div></div>
  <div class="progress" aria-label="Directional probability indicator"><div id="confidence-fill"></div></div>
  <div class="note" id="confidence-note">A final forecast requires more than 69% predicted-direction probability at the eight-minute checkpoint, plus the near-strike flip-risk check.</div>
  <div class="two-times">
   <div><div class="time-label">Round closes in</div><div class="time-value" id="round-countdown">—</div></div>
   <div class="right"><div class="time-label" id="lock-time-label">Forecast locks at</div><div class="time-value" id="lock-time">—</div></div>
  </div>
  <details id="record-details"><summary>Forecast record details</summary><div class="detail-grid">
   <span>Model</span><strong id="detail-model">—</strong>
   <span>Final directional probability</span><strong id="detail-probability">—</strong>
   <span>Forecast method</span><strong id="detail-method">—</strong>
   <span>Model strength (gate)</span><strong id="detail-strength">—</strong>
   <span>Model hash</span><strong id="detail-hash">—</strong>
   <span>Record status</span><strong id="detail-status">—</strong>
   <span>Reason</span><strong id="detail-reason">—</strong>
   <span>Coin-flip risk</span><strong id="detail-coinflip-risk">—</strong>
   <span>Distance from strike</span><strong id="detail-coinflip-z">—</strong>
  </div></details>

  <section class="scalp-monitor" aria-label="Continuous intramarket reversal monitor">
   <div class="scalp-monitor-row">
    <span class="scalp-monitor-title">REVERSAL RISK</span>
    <span class="scalp-risk-value" id="scalp-reversal-risk">UNAVAILABLE</span>
   </div>
   <div class="scalp-monitor-note" id="scalp-reversal-detail">Analyzing live model and Kalshi market quotes…</div>
   <div class="scalp-flip-row">
    <div class="scalp-monitor-row">
     <span class="scalp-monitor-title">FLIP ALERTS</span>
     <span class="scalp-flip-status" id="scalp-flip-alert" role="status" aria-live="polite">Awaiting live evidence</span>
    </div>
    <div class="scalp-monitor-note" id="scalp-flip-detail" style="margin-bottom:0">Potential shifts only; no automatic trade execution.</div>
   </div>
   <div class="scalp-monitor-disclaimer">Early flip warnings are unvalidated heuristics, not guaranteed directional predictions or trade signals.</div>
  </section>
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
   <div class="health-row"><span>Checkpoint rule</span><strong>480 seconds before close</strong></div>
   <div class="health-row"><span>SKIP threshold</span><strong>69% predicted-direction probability (SKIP at or below)</strong></div>
   <div class="health-row"><span>Latest prediction</span><strong id="latest-at">—</strong></div>
   <div class="health-row"><span>Market closes</span><strong id="close-at">—</strong></div>
  </article>
 </div>
</section>
<section class="card training-card" aria-label="Adaptive model training">
 <div class="training-head">
  <div>
   <div class="eyebrow">TRAIN / MODEL EVOLUTION</div>
   <h2>Train. Verify. Improve.</h2>
   <p class="subtitle">One training control. Every stage recorded. Only validated, compatible models can be activated.</p>
  </div>
  <div class="training-head-controls">
   <div class="training-model-identity">
    <span>RUNNING MODEL</span>
    <strong id="active-model-number">—</strong>
    <small id="installed-model-number">Checking promoted model identity…</small>
   </div>
   <span class="training-pill waiting" id="train-status-pill">CHECKING DATA</span>
  </div>
 </div>
 <div class="training-forward" aria-label="Prospective eight-minute predictor training">
  <div class="training-forward-head">
   <div><div class="training-forward-title" id="training-evidence-title">Verified 480-second predictor training</div>
    <div id="forward-480-eligible" class="training-forward-numbers">Checking verified markets…</div>
   </div>
   <strong id="forward-480-progress" class="training-big-percent">0%</strong>
  </div>
  <div class="training-bar" aria-label="Evidence readiness"><div id="forward-480-bar-fill"></div></div>
  <p id="forward-480-reason" class="training-forward-note">Checking independent, settled model evidence.</p>
  <div id="baseline-retrain-ready" class="training-forward-note">Checking full-model retraining readiness…</div>
  <div class="training-launch">
   <div>
    <h3 id="training-headline">Checking training readiness</h3>
    <p id="training-description">Training will use the eligible verified dataset, never simulated examples.</p>
   </div>
   <div class="training-actions">
    <button class="train-button" type="button" disabled id="train-now">Train Now</button>
    <button class="train-pause-button" type="button" disabled id="train-pause">Pause</button>
    <button class="train-resume-button" type="button" disabled id="train-resume">Resume</button>
   </div>
  </div>
 </div>
 <div class="training-live-panel" aria-label="Training session and validation results">
  <div class="training-live-head">
   <div><div class="eyebrow">LIVE TRAINING SESSION</div><h3 id="training-stage-heading">Training progress</h3></div>
   <strong class="training-big-percent" id="training-worker-progress">—</strong>
  </div>
  <div class="training-bar training-worker-bar" role="progressbar" aria-label="Completed training milestones" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="training-worker-bar"><div id="training-worker-bar-fill"></div></div>
  <div class="training-stage-list" id="training-stage-list" aria-label="Model training milestones"></div>
  <div class="training-forward-note" id="dataset-build-detail">Dataset has not been built in this session yet.</div>
  <div class="training-bar training-dataset-bar" role="progressbar" aria-label="Current training stage progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="dataset-build-bar"><div id="dataset-build-bar-fill"></div></div>
  <div class="training-comparison" id="training-comparison" aria-label="Challenger versus current model"></div>
  <div id="training-outcome" class="training-outcome" role="status" aria-live="polite">No training run yet. Training results will appear here and remain after refreshing the page.</div>
  <section id="training-promotion-audit" class="training-audit" aria-label="Post-training eight-minute model comparison" hidden></section>
  <div class="train-feedback" id="train-feedback" role="status" aria-live="polite"></div>
  <div id="forward-480-result" class="training-forward-note">Model training and evaluation are combined into Train Now. A high validation score is not automatically permission for live trading.</div>
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
<footer>Read-only research tool. Model confidence is a directional-strength score and is not the same as measured forecast accuracy. At the 69% directional-probability gate, predictions at or below 69% are skipped by the model's current definition; this is not a guarantee of correctness. History excludes pending and SKIP forecasts from directional accuracy.</footer>
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
/* Reuse verified holdings from the Trade card's existing authenticated GET.
   Never let a model forecast or a simulated order impersonate an owned position. */
let positionLive=null;
document.addEventListener("kalshiedge:position-snapshot",event=>{
 const item=event.detail;
 positionLive=item&&typeof item==="object"&&typeof item.ticker==="string"&&
    /^KXBTC15M-[A-Za-z0-9-]+$/.test(item.ticker)?item:null;
 renderPositionBanner(live);
});
function exactPnlAmount(raw){
 const parts=raw.replace(/^-/, "").split(".");
 const fraction=(parts[1]||"").padEnd(4,"0");
 return parts[0]+"."+(fraction==="0000"||fraction.endsWith("00")
    ?fraction.slice(0,2):fraction);
}
function renderPositionBanner(data){
 const banner=el("lock-banner");
 const details=el("lock-description");
 const now=Date.now();
 const status=positionLive,valid=!!status&&
   (!data?.market_ticker||status.ticker===data.market_ticker)&&
   Number.isFinite(status.observed_at_ms)&&
   now>=status.observed_at_ms&&now-status.observed_at_ms<=10000;
 if(!valid){
  banner.className="lock-banner position-flat";
  details.className="lock-desc pnl-pending";
  text("lock-heading","POSITION UPDATING");
  text("lock-description","UNREALIZED P/L —");
  return;
 }
 const raw=status.position_yes;
 if(typeof raw!=="string"||!/^-?[0-9]+(?:[.][0-9]+)?$/.test(raw)){
  banner.className="lock-banner position-flat";
  details.className="lock-desc pnl-pending";
  text("lock-heading","POSITION UPDATING");
  text("lock-description","UNREALIZED P/L —");
  return;
 }
 const held=Number(raw);
 if(!Number.isFinite(held)||held===0){
  const sold=status.last_sell;
  const confirmed=!!sold&&sold.status==="SOLD"&&sold.ticker===status.ticker;
  banner.className="lock-banner position-flat";
  text("lock-heading",confirmed?"SOLD":"NO POSITION");
  const profit=confirmed&&typeof sold.realized_pnl_dollars==="string"&&
    /^-?[0-9]+(?:[.][0-9]+)?$/.test(sold.realized_pnl_dollars)
      ?Number(sold.realized_pnl_dollars):NaN;
  if(!Number.isFinite(profit)){
   details.className="lock-desc pnl-pending";
   text("lock-description",confirmed?"REALIZED P/L —":"UNREALIZED P/L —");
  }else{
   const amount=exactPnlAmount(sold.realized_pnl_dollars);
   details.className="lock-desc "+(profit<0?"pnl-loss":"pnl-gain");
   text("lock-description","REALIZED P/L "+(profit<0?"−":"+")+"$"+amount+" est.");
  }
  return;
 }
 const up=held>0;
 banner.className="lock-banner "+(up?"position-up":"position-down");
 text("lock-heading",up?"BOUGHT UP":"BOUGHT DOWN");
 const value=status.position_unrealized_pnl_dollars;
 const validPnl=status.position_pnl_status==="ESTIMATED"&&
   typeof value==="string"&&/^-?[0-9]+(?:[.][0-9]+)?$/.test(value)&&
   Number.isFinite(Number(value));
 if(!validPnl){
  details.className="lock-desc pnl-pending";
  text("lock-description","UNREALIZED P/L —");
  return;
 }
 const profit=Number(value);
 const shown=exactPnlAmount(value);
 details.className="lock-desc "+(profit<0?"pnl-loss":"pnl-gain");
 text("lock-description","UNREALIZED P/L "+(profit<0?"−":"+")+"$"+shown+" est.");
}
function renderScalpMonitor(data){
 const risk=data.reversal_risk||{},level=["LOW","MEDIUM","HIGH"].includes(risk.level)&&data.status==="LIVE"
   ?risk.level:"UNAVAILABLE";
 const badge=el("scalp-reversal-risk");
 badge.className="scalp-risk-value "+(level==="UNAVAILABLE"?"":level.toLowerCase());
 badge.textContent=level;
 const good=level!=="UNAVAILABLE"&&Number.isFinite(risk.observed_ts_ns)&&Number.isFinite(data.server_time_ns);
 const age=good?Math.max(0,Math.round((data.server_time_ns-risk.observed_ts_ns)/1e9)):null;
 const driverLabels={
  MODEL_ADVERSE_TREND:"Model momentum",KALSHI_QUOTE_ADVERSE_TREND:"Kalshi quote pressure",
  MODEL_NEAR_50:"Model near 50%",NEAR_STRIKE:"BTC near strike",
  MODEL_DISAGREEMENT:"Model disagreement",WIDE_SPREAD:"Wide spread",
  WIDENING_SPREAD:"Spread widening",ADVERSE_QUEUE_IMBALANCE:"Bid/ask imbalance",
  ADVERSE_TAKER_FLOW:"Aggressive trades",BRTI_VOLATILITY_PRESSURE:"Strike volatility",
  EARLY_FLIP_PRESSURE:"Early flip pressure"
 };
 const drivers=Array.isArray(risk.risk_drivers)?risk.risk_drivers
   .filter(key=>Object.hasOwn(driverLabels,key)).map(key=>driverLabels[key]).slice(0,3):[];
 const quality=["FULL","PARTIAL"].includes(risk.signal_quality)?risk.signal_quality:"BASE";
 const score=Number.isFinite(risk.risk_score)?" · Stress index "+risk.risk_score+"/100 (heuristic)":"";
 text("scalp-reversal-detail",good
    ?"5/15/30s multi-signal · "+quality+" evidence"+score+
      (drivers.length?" · "+drivers.join(", "):" · No major stress drivers")+
      " · "+age+"s old"
    :"Insufficient, unhealthy or stale live market data; not safe to classify.");
 const alert=good?risk.flip_alert:null;
 const flip=el("scalp-flip-alert");
 if(alert&&alert.kind==="PRE_FLIP_WARNING"&&["UP","DOWN"].includes(alert.direction)
   &&alert.current_direction===(alert.direction==="DOWN"?"UP":"DOWN")
   &&Number.isFinite(alert.age_seconds)&&alert.age_seconds<=5){
   flip.className="scalp-flip-status active "+(alert.direction==="DOWN"?"down":"up");
   flip.textContent=alert.direction;
   const names={model:"Model",kalshi_quote:"Kalshi price",brti:"BRTI",
     queue_imbalance:"Book depth",taker_flow:"Trade flow",binance:"Binance"};
   const evidence=Array.isArray(alert.supporting_signals)?
     alert.supporting_signals.filter(key=>Object.hasOwn(names,key)).map(key=>names[key]).slice(0,5):[];
   text("scalp-flip-detail","Early warning: model still "+alert.current_direction+
     " · "+(evidence.length?evidence.join(" + "):"model and Kalshi quote")+
     " pointing "+alert.direction+" · unconfirmed.");
 }else{
   flip.className="scalp-flip-status";
   flip.textContent=good?"MONITORING":"UNAVAILABLE";
   text("scalp-flip-detail",good
     ?"No qualifying pre-flip warning. Watching live model and Kalshi quote movement."
     :"Flip warnings paused until fresh market data is restored.");
 }
}
function renderLive(data){
 renderScalpMonitor(data);
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
 renderPositionBanner(data);
 const side=p?.status==="OK"?p.predicted_side:null;
 const display=side==="ABOVE"?"UP":side==="BELOW"?"DOWN":side==="SKIP"?"SKIP":current?"WAITING":"WAITING";
 const direction=el("direction");direction.className="direction "+(display==="UP"?"up":display==="DOWN"?"down":display==="SKIP"?"skip":"waiting");direction.textContent=display;
 text("direction-caption",p?.status==="OK"?"Live settlement-direction estimate · updates through market close, not a scalp-return forecast.":current?"Live signal unavailable: "+(p?.reason||"waiting for healthy feeds"):"No currently readable market and prediction.");
 const strength=locked?f.confidence:p?.confidence;
 const sideProbability=p?.status==="OK"?(p.predicted_side==="ABOVE"?p.p_yes:p.predicted_side==="BELOW"?p.p_no:null):null;
 text("confidence",Number.isFinite(sideProbability)?Math.round(sideProbability*100)+"%":"—");
 el("confidence-fill").style.width=Number.isFinite(sideProbability)?Math.min(100,Math.max(0,sideProbability*100))+"%":"0%";
 text("confidence-sub",p?.status==="OK"?(p?.fusion_hash?"Fitted multi-signal · live":"Live model · updates until close"):"Awaiting healthy live inputs");
 text("confidence-note","Live probability describes settlement direction, not historical accuracy or short-horizon scalp returns. The separate 8-minute record is immutable for history.");
 text("detail-model",f?.model_version||p?.model_version);
 text("detail-probability",pct(locked?f.directional_probability:sideProbability));
 text("detail-method",f?.fusion_hash||p?.fusion_hash?"Fitted multi-signal at 480s":"Base model (no fitted fusion)");
 text("detail-strength",pct(strength));
 text("detail-hash",f?.model_hash||p?.model_hash||"—");
 renderModelIdentity();
 text("detail-status",locked?"IMMUTABLE":p?.status||"WAITING");
 text("detail-reason",locked?f.reason||"OK":p?.reason||"—");
 text("detail-coinflip-risk",locked?(f.coin_flip_assessment||"UNAVAILABLE"):"AWAITING LOCK");
 text("detail-coinflip-z",locked&&Number.isFinite(f.coin_flip_risk_z)?f.coin_flip_risk_z.toFixed(2)+"σ":"—");
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
let liveRefreshPending=false;
async function refreshLive(){
 if(liveRefreshPending)return;
 liveRefreshPending=true;
 try{
   const response=await fetch("/api/live",{cache:"no-store"});
   if(!response.ok)throw Error("HTTP "+response.status);
   renderLive(await response.json());
 }catch(err){
   el("connection-dot").classList.remove("on");
   text("connection-label","Dashboard disconnected");
   text("feed-status","DASHBOARD ERROR");
   renderScalpMonitor({status:"DEGRADED",reversal_risk:{level:"UNAVAILABLE"}});
   console.warn(err);
 }finally{
   liveRefreshPending=false;
 }
}
async function refreshHistory(){try{const response=await fetch("/api/history",{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);renderHistory(await response.json())}
catch(err){text("history-status","Could not refresh: "+err.message)}}
el("filter")?.addEventListener("change",()=>{if(history)renderHistory(history)});
let trainingStatus=null;
let trainingActionError="";
const trainingStages=[
 ["BUILDING_DATASET","Create dataset"],
 ["VERIFYING_DATASET","Audit dataset"],
 ["SPLITTING","Split markets"],
 ["FITTING","Fit model"],
 ["SCORING","Score holdout"],
 ["COMPARING","Compare results"],
 ["PROMOTION_GATE","Deployment gate"]
];
function trainPercent(n){return typeof n==="number"&&Number.isFinite(n)?(100*n).toFixed(1)+"%":"—"}
// Source progress is authoritative. The animation never exceeds a verified
// market count and never increases filesystem writes or model workload.
const datasetTicker={
 runId:null,phase:null,total:0,shown:0,target:0,raf:0,active:false,
 valid:false,hasRun:false,prefix:"",action:""
};
function advanceVerifiedMarketCounter(shown,verified){
 return shown<verified?shown+1:verified;
}
// Don't round 29,855 / 29,865 to a misleading 100.0%.
function formatDatasetPercent(completed,total){
 if(!Number.isFinite(total)||total<=0)return "0.0";
 const pct=100*Math.min(total,Math.max(0,completed))/total;
 return (completed<total?Math.min(99.9,Math.floor(pct*10)/10):100).toFixed(1);
}
function paintVerifiedMarketCounter(){
 const ticker=datasetTicker;
 const pct=ticker.valid?
  Math.min(100,Math.max(0,100*ticker.shown/ticker.total)):0;
 const counts=ticker.valid?
  " · "+ticker.shown+" / "+ticker.total+
  (ticker.phase==="labels"?" market files verified (":
   ticker.phase==="checkpoints"?" checkpoint records verified (":" markets checked (")+
  formatDatasetPercent(ticker.shown,ticker.total)+"%)":
  (ticker.hasRun&&ticker.active?" · counting verified market files…":"");
 el("dataset-build-bar-fill").style.width=pct+"%";
 el("dataset-build-bar").setAttribute("aria-valuenow",String(Math.round(pct)));
 text("dataset-build-detail",ticker.hasRun?
  ticker.prefix+counts+" · "+ticker.action:
  trainingStatus?.current_model?.promoted_hash?
   "Continue Training reuses verified 480s snapshots from the active model; it does not rebuild the historical dataset.":
   "Dataset creation begins when you press Train Now; it uses only verified source data.");
}
function tickVerifiedMarketCounter(){
 datasetTicker.raf=0;
 if(document.hidden||!datasetTicker.active)return;
 datasetTicker.shown=advanceVerifiedMarketCounter(
  datasetTicker.shown,datasetTicker.target);
 paintVerifiedMarketCounter();
 if(datasetTicker.shown<datasetTicker.target){
  datasetTicker.raf=requestAnimationFrame(tickVerifiedMarketCounter);
 }
}
function renderVerifiedMarketCounter(run,hasRun,failed,running){
 const completed=Number(run.dataset_markets_completed);
 const total=Number(run.dataset_markets_total);
 const valid=Number.isFinite(completed)&&Number.isFinite(total)&&
  Number.isInteger(completed)&&Number.isInteger(total)&&total>0&&completed>=0;
 const phase=run.dataset_progress_phase==="labels"?"labels":
  run.dataset_progress_phase==="checkpoints"?"checkpoints":"markets";
 const runId=String(run.run_id||run.started_utc||"unknown");
 const changed=runId!==datasetTicker.runId||
  phase!==datasetTicker.phase||total!==datasetTicker.total;
 const active=!!running&&run.stage==="BUILDING_DATASET"&&valid;
 const count=valid?Math.min(completed,total):0;
 if(changed||!active||document.hidden){
  if(datasetTicker.raf)cancelAnimationFrame(datasetTicker.raf);
  datasetTicker.raf=0;
  datasetTicker.shown=count;
  datasetTicker.target=count;
 }else{
  // Ignore stale full-status snapshots. Never animate above confirmed count.
  datasetTicker.target=Math.max(datasetTicker.target,count);
 }
 datasetTicker.runId=runId;
 datasetTicker.phase=phase;
 datasetTicker.total=total;
 datasetTicker.valid=valid;
 datasetTicker.active=active;
 datasetTicker.hasRun=hasRun;
 datasetTicker.prefix=run.dataset_verified?"Dataset verified":
  failed?"Dataset build stopped":"Dataset processing";
 datasetTicker.action=run.dataset_action||
  "Checking official settlement and source integrity";
 paintVerifiedMarketCounter();
 if(active&&!document.hidden&&datasetTicker.shown<datasetTicker.target&&
   !datasetTicker.raf){
  datasetTicker.raf=requestAnimationFrame(tickVerifiedMarketCounter);
 }
}
document.addEventListener("visibilitychange",()=>{
 if(document.hidden&&datasetTicker.raf){
  cancelAnimationFrame(datasetTicker.raf);datasetTicker.raf=0;
 }else if(!document.hidden&&datasetTicker.active&&
          datasetTicker.shown<datasetTicker.target&&!datasetTicker.raf){
  datasetTicker.raf=requestAnimationFrame(tickVerifiedMarketCounter);
 }
});
function stageProgressState(run){
 const stage=String(run.stage||"");
 if(stage==="BUILDING_DATASET"){
  if(run.mode==="continue_live")return {
   mode:"dataset",label:"Reuse saved snapshots",
   detail:String(run.dataset_action||"Checking existing verified 480s market snapshots"),
  };
  return {mode:"dataset",label:"Create dataset",detail:"Preparing verified historical markets"};
 }
 if(stage==="FITTING"||stage==="TRAINING"){
  const completed=Number(run.fit_steps_completed);
  const total=Number(run.fit_steps_total);
  const valid=run.fit_steps_completed!=null&&run.fit_steps_total!=null&&
   Number.isInteger(completed)&&Number.isInteger(total)&&
   total>0&&completed>=0&&completed<=total;
  return {mode:"fit",label:"Fit model",completed:valid?completed:null,
   total:valid?total:null,
   detail:String(run.fit_step_detail||
     "Fitting verified model candidates; waiting for the next completed operation")};
 }
 if(stage==="SCORING"||stage==="COMPARING"){
  const completed=Number(run.score_rows_completed);
  const total=Number(run.score_rows_total);
  const valid=run.score_rows_completed!=null&&run.score_rows_total!=null&&
   Number.isInteger(completed)&&Number.isInteger(total)&&
   total>0&&completed>=0&&completed<=total;
  if(valid){
   return {mode:"score",label:stage==="SCORING"?"Score holdout":"Compare results",
    completed,total,
    detail:String(run.score_detail||"Completed verified structural scoring rows")};
  }
 }
 const labels={
  STARTING:"Starting training",
  VERIFYING:"Audit dataset",VERIFYING_DATASET:"Audit dataset",
  SPLITTING:"Split markets",
  EVALUATING:"Score holdout",SCORING:"Score holdout",
  COMPARING:"Compare results",PROMOTION_GATE:"Deployment gate",
  COMPLETE:"Training complete",FAILED:"Training failed",
  INTERRUPTED:"Training interrupted",PAUSED:"Training paused",
  RESUMING:"Resuming training"
 };
 const details={
  STARTING:"Verifying training eligibility and model identity",
  VERIFYING:"Checking saved dataset and partition integrity",
  VERIFYING_DATASET:"Checking dataset source hashes and partition validity",
  SPLITTING:"Preparing leakage-safe chronological market splits",
  EVALUATING:"Loading the reserved independent holdout",
  SCORING:"Scoring the candidate on untouched holdout markets",
  COMPARING:"Comparing candidate and existing model probabilities",
  PROMOTION_GATE:"Checking accuracy, coverage and promotion safeguards",
  COMPLETE:"All verified stages finished",
  FAILED:"Stopped; live model remains unchanged",
  INTERRUPTED:"Worker interrupted; live model remains unchanged",
  PAUSED:"Waiting for Resume; saved checkpoints remain available",
  RESUMING:"Revalidating saved work before resuming"
 };
 return {mode:"stage",label:labels[stage]||stage.replaceAll("_"," ")||"Not started",
  detail:details[stage]||"Checking current training worker status"};
}
function renderCurrentStageProgress(run,running,hasRun,failed){
 const display=stageProgressState(run);
 const bar=el("dataset-build-bar");
 const mapped=run.stage==="TRAINING"?"FITTING":
  run.stage==="EVALUATING"?"SCORING":
  run.stage==="VERIFYING"?"VERIFYING_DATASET":run.stage;
 const index=trainingStages.findIndex(([stage])=>stage===mapped);
 text("training-stage-heading",hasRun&&index>=0?
  "Stage "+(index+1)+" of 7 · "+display.label:
  "Training progress · "+display.label);
 if(display.mode==="dataset"){
  bar.hidden=false;
  bar.setAttribute("aria-label","Dataset preparation progress");
  renderVerifiedMarketCounter(run,hasRun,failed,running);
  return;
 }
 // Durable Stage 1 dataset fields are irrelevant after Stage 1.
 if(datasetTicker.raf)cancelAnimationFrame(datasetTicker.raf);
 datasetTicker.raf=0;
 datasetTicker.active=false;
 if(display.mode==="score"&&display.total!==null){
  const pct=100*display.completed/display.total;
  bar.hidden=false;
  bar.setAttribute("aria-label","Completed holdout scoring rows");
  el("dataset-build-bar-fill").style.width=pct+"%";
  bar.setAttribute("aria-valuenow",String(Math.round(pct)));
  text("dataset-build-detail",display.label+" · "+
   display.completed+" / "+display.total+" verified rows scored ("+
   formatDatasetPercent(display.completed,display.total)+"%) · "+display.detail);
  return;
 }
 if(display.mode==="fit"&&display.total!==null){
  const pct=100*display.completed/display.total;
  bar.hidden=false;
  bar.setAttribute("aria-label","Completed model fitting operations");
  el("dataset-build-bar-fill").style.width=pct+"%";
  bar.setAttribute("aria-valuenow",String(Math.round(pct)));
  text("dataset-build-detail","Stage 4 · Fit model · "+
   display.completed+" / "+display.total+" fitting operations completed ("+
   formatDatasetPercent(display.completed,display.total)+"%) · "+display.detail);
 }else{
  // A stage without a trustworthy counter has no fabricated progress bar.
  bar.hidden=true;
  text("dataset-build-detail",display.label+" · "+display.detail+
   (display.mode==="fit"?" · Waiting for confirmed fitting operations":""));
 }
}
function renderPromotionAudit(run){
 const panel=el("training-promotion-audit");
 panel.replaceChildren();
 const audit=run.promotion_audit;
 if(!audit||typeof audit!=="object"||!audit.status){
  panel.hidden=true;return;
 }
 panel.hidden=false;
 const heading=document.createElement("h4");
 heading.textContent="Post-training model comparison · 480 seconds";
 panel.append(heading);
 function line(value,note=false){
  const item=document.createElement("div");
  item.className=note?"training-audit-note":"training-audit-line";
  item.textContent=value;panel.append(item);
 }
 const n=Number(audit.market_count)||0;
 const fixed=(v,decimals=4)=>typeof v==="number"&&Number.isFinite(v)?
  v.toFixed(decimals):"unavailable";
 const delta=v=>typeof v==="number"&&Number.isFinite(v)?
  (v>0?"+":"")+fixed(v,4):"unavailable";
 line("Overall directional accuracy: candidate "+
  trainPercent(audit.overall_480_accuracy_candidate)+" vs current "+
  trainPercent(audit.overall_480_accuracy_incumbent)+
  " · difference "+delta(audit.overall_480_accuracy_delta_pp)+" pp ("+n+" markets).");
 line("Qualifying forecasts: "+audit.candidate_qualified+" vs "+
  audit.incumbent_qualified+" · coverage difference "+
  delta(audit.coverage_delta_pp)+" pp; qualifying accuracy difference "+
  delta(audit.qualified_accuracy_delta_pp)+" pp.");
 line("Probability score differences (candidate minus current; lower is better): "+
  "Brier "+delta(audit["480_brier_delta"])+
  " · log loss "+delta(audit["480_log_loss_delta"])+
  " · ECE "+delta(audit["480_ece_delta"])+".");
 line("All-row directional accuracy difference: "+
  delta(audit.overall_allrows_accuracy_delta_pp)+" pp.");
 const paired=audit.paired_480;
 if(paired&&typeof paired==="object"){
  line("Same-market outcomes: both correct "+paired.both_correct+
   " · candidate-only correct "+paired.candidate_only_correct+
   " · current-only correct "+paired.incumbent_only_correct+
   " · both wrong "+paired.both_wrong+".");
  line("Qualification overlap: both "+paired.both_qualified+
   " · candidate-only "+paired.candidate_only_qualified+
   " · current-only "+paired.incumbent_only_qualified+".");
  if(Array.isArray(paired.candidate_only_correct_tickers)&&
     Array.isArray(paired.incumbent_only_correct_tickers)){
   const details=document.createElement("details");
   const summary=document.createElement("summary");
   summary.textContent="View markets where the models disagreed";
   const cases=document.createElement("div");
   cases.className="training-audit-note";
   cases.textContent="Candidate-only correct: "+
    (paired.candidate_only_correct_tickers.join(", ")||"none")+
    " · Current-only correct: "+
    (paired.incumbent_only_correct_tickers.join(", ")||"none")+
    " · Candidate-only qualifying: "+
    (Array.isArray(paired.candidate_only_qualified_tickers)?
     (paired.candidate_only_qualified_tickers.join(", ")||"none"):"unavailable")+
    " · Current-only qualifying: "+
    (Array.isArray(paired.incumbent_only_qualified_tickers)?
     (paired.incumbent_only_qualified_tickers.join(", ")||"none"):"unavailable");
   details.append(summary,cases);
   panel.append(details);
  }
  const ci=paired.confidence_interval_95_pp;
  if(Array.isArray(ci)&&ci.length===2){
   line("Paired 95% bootstrap interval for accuracy difference: ["+
    delta(ci[0])+", "+delta(ci[1])+"] pp; exact McNemar p="+
    fixed(paired.mcnemar_exact_p,4)+".",true);
  }
  line("Uncertainty is descriptive and does not override deployment gates.",true);
 }else{
  line(audit.paired_status||"Paired outcomes not recorded for this evaluation.",true);
 }
 const economics=audit.trade_economics||{};
 line("Net trading profit: "+String(economics.status||"UNAVAILABLE")+
  " · "+String(economics.reason||"No verified execution data."),true);
}
function renderTrainingSession(run,running){
 const events=Array.isArray(run.events)?run.events:[];
 const current=String(run.stage||"");
 const done=current==="COMPLETE";
 const failed=current==="FAILED"||current==="INTERRUPTED";
 const mapped=x=>x==="TRAINING"?"FITTING":x==="EVALUATING"?"SCORING":
  x==="VERIFYING"?"VERIFYING_DATASET":x;
 const doneStages=events.map(e=>mapped(String(e.stage||"")));
 const currentMapped=mapped(current);
 const list=el("training-stage-list");
 list.replaceChildren();
 text("training-evidence-title",trainingStatus?.current_model?.promoted_hash?
  "New settled 480-second markets · current model":
  "Verified 480-second predictor training");
 for(let i=0;i<trainingStages.length;i++){
  const [stage,originalLabel]=trainingStages[i];
  const continuationLabels={
   BUILDING_DATASET:"Reuse saved snapshots",
   VERIFYING_DATASET:"Audit settled evidence",
   SPLITTING:"Split fresh markets",
   FITTING:"Continue fitted weights",
   SCORING:"Score 80 fresh markets",
   COMPARING:"Compare existing models",
   PROMOTION_GATE:"Deployment gate"
  };
  const label=run.mode==="continue_live"?
   (continuationLabels[stage]||originalLabel):originalLabel;
  const stageDiv=document.createElement("div");
  stageDiv.className="training-stage"+(doneStages.includes(stage)?" done":"")+
   (!done&&!failed&&currentMapped===stage?" current":"")+
   (failed&&currentMapped===stage?" failed":"");
  const number=document.createElement("span");number.className="step-id";
  number.textContent="STAGE "+(i+1);
  const name=document.createElement("div");name.className="step-name";name.textContent=label;
  stageDiv.append(number,name);list.append(stageDiv);
 }
 const hasRun=!!run.stage;
 renderCurrentStageProgress(run,running,hasRun,failed);
 const pct=hasRun?Math.max(0,Math.min(100,Number(run.progress_percent)||0)):0;
 text("training-worker-progress",hasRun?(failed?"FAILED":pct+"%"):"—");
 el("training-worker-bar-fill").style.width=pct+"%";
 el("training-worker-bar").setAttribute("aria-valuenow",String(pct));
 const comparison=el("training-comparison");
 comparison.replaceChildren();
 const r=run.results||{};
 const candidateGate=r.candidate_gate||r.candidate_69_gate;
 const incumbentGate=r.incumbent_gate||r.incumbent_69_gate;
 const recordedStrengthGate=Number(r.gate_confidence); // historical provenance only
 // Historical training results were evaluated at the policy in force
 // when recorded, often a *strength* gate. Do not display that percentage
 // as if it were today's directional-probability SKIP threshold.
 if(candidateGate&&incumbentGate){
  const info=document.createElement("small");
  info.className="training-comparison-note";
  info.textContent="Current live gate: >69% predicted-direction probability. Training scores below are a historical strength evaluation, not today's live-gate result."+
   (Number.isFinite(recordedStrengthGate)?" The original evaluation threshold remains in the saved report.":"");
  comparison.append(info);
  const metrics=[
   ["Newly trained model",candidateGate],
   ["Current live model",incumbentGate]
  ];
  for(const [title,data] of metrics){
   const card=document.createElement("div");card.className="training-metric";
   const h=document.createElement("h4");h.textContent=title+" · historical strength evaluation";
   const strong=document.createElement("strong");
   strong.textContent=trainPercent(data.qualified_accuracy);
   const caption=document.createElement("small");
   // Completed continuation runs store raw GateSnapshot field names, while
   // earlier full-retrain snapshots store compact counts. Preserve either.
   // Missing counts must never be presented as a fabricated 0/0.
   const qualified=data.qualified??data.qualified_markets;
   const correct=data.correct??data.qualified_correct;
   const validCounts=Number.isInteger(qualified)&&Number.isInteger(correct)&&
    correct>=0&&qualified>=correct;
   caption.textContent=(validCounts
    ?String(correct)+"/"+String(qualified)+" correct · "+String(qualified)+" forecasts"
    :"Counts unavailable")+
    " · "+trainPercent(data.coverage)+" coverage";
   card.append(h,strong,caption);comparison.append(card);
  }
 }
 renderPromotionAudit(run);
 const outcome=el("training-outcome");
 outcome.className="training-outcome";
 const reasons=Array.isArray(run.reasons)?run.reasons:[];
 if(!hasRun){
  outcome.textContent="No training run yet. Training steps, model comparisons, and the final decision will appear here and remain after refreshing.";
 }else if(failed){
  outcome.classList.add("fail");
  outcome.textContent="TRAINING FAILED — Existing model unchanged.\n"+
   (run.error||reasons.join("\n")||"Worker interrupted; inspect training log.");
 }else if(current==="PAUSED"){
  outcome.classList.add("paused");
  outcome.textContent="TRAINING PAUSED · Saved checkpoint: "+
   String(run.resume_stage||"last verified stage").replaceAll("_"," ")+
   "\nPress Resume to continue this same training run. Completed dataset markets are preserved. An unfinished model fitting stage may need to rerun.";
 }else if(!done){
  outcome.textContent="IN PROGRESS · "+
   (current.replaceAll("_"," ")||"Starting")+
   "\nCompleted milestones: "+pct+"%. This is stage progress, not an elapsed-time estimate.";
 }else if(run.decision==="PROMOTED"&&run.promoted===true){
  outcome.classList.add("pass");
  outcome.textContent="PASS · NEW MODEL PROMOTED.\nThe verified candidate passed the independent evaluation and incumbent checks. Restart KalshiEdge to load the newly promoted model.\n"+
   reasons.join("\n");
 }else if(run.decision==="TRAINED_NOT_PROMOTED"){
  outcome.classList.add("pending");
  outcome.textContent="CONTINUED TRAINING SAVED · Current model retained.\n"+
   (reasons.length?reasons.join("\n"):
    "The previous classifier weights were extended and checkpointed. More new markets are needed to validate promotion.");
 }else if(run.decision==="VALIDATION_FAILED"){
  outcome.classList.add("fail");
  outcome.textContent="FAIL · NEW MODEL REJECTED — Existing model retained.\n"+
   (reasons.length?reasons.join("\n"):"Independent validation gates not passed.");
 }else if(run.decision==="NOT_DEPLOYABLE"){
  outcome.classList.add("pending");
  outcome.textContent="VALIDATION IMPROVED · NOT ACTIVATED.\n"+
   (reasons.length?reasons.join("\n"):"Independent prospective evidence and live model compatibility still required.");
 }else{
  outcome.classList.add("pending");
  outcome.textContent="NOT PROMOTED · "+(run.decision||"Evaluation incomplete")+
   "\n"+(reasons.length?reasons.join("\n"):"See the verified evaluation report.");
 }
 text("forward-480-result",done?
  "Report: "+(run.report_path||run.evaluation_report||"see local training logs")+
  " · Promotion status: "+(run.promoted===true?"PROMOTED":"NOT PROMOTED"):
  "Stages update from the local training worker; no fictitious completion estimate.");
}
function renderModelIdentity(){
 const identity=trainingStatus?.current_model||{};
 const n=identity.model_number;
 const hash=identity.promoted_hash;
 const loadedHash=live?.latest_prediction?.model_hash||live?.final_forecast?.model_hash;
 if(!Number.isInteger(n)||!hash){
  text("active-model-number","UNAVAILABLE");
  text("installed-model-number","No verified promoted model pointer");
  return;
 }
 if(!loadedHash){
  text("active-model-number","MODEL #"+n);
  text("installed-model-number","Installed · waiting for live-process confirmation");
 }else if(loadedHash===hash){
  text("active-model-number","MODEL #"+n);
  text("installed-model-number","Running and installed · hash matched");
 }else if(loadedHash===identity.previous_model_hash&&Number.isInteger(identity.previous_model_number)){
  text("active-model-number","MODEL #"+identity.previous_model_number);
  text("installed-model-number","Model #"+n+" installed · Restart to activate");
 }else{
  text("active-model-number","MODEL ?");
  text("installed-model-number","Model #"+n+" installed · loaded model hash differs");
 }
}
// Only routed /train installs session status persistence; no browser storage
// is used on API credential and trading pages.
function saveTrainingSnapshot(info){}
function restoreTrainingSnapshot(){return false;}
function updateTrainingPauseControls(info){
 const run=info.run||{};
 const running=!!info.running;
 const requested=!!info.pause_requested;
 const paused=!!info.paused||(!running&&run.stage==="PAUSED");
 el("train-pause").disabled=!running||requested||run.stage==="PAUSED";
 el("train-pause").textContent=requested?"Pausing…":"Pause";
 el("train-resume").disabled=!paused||running;
 el("train-resume").textContent="Resume";
 el("train-now").disabled=paused||requested||running||!info.train_available;
 const pill=el("train-status-pill");
 if(requested&&running){pill.textContent="PAUSE_REQUESTED";}
 else if(paused){pill.textContent="PAUSED";}
 else if(run.stage==="RESUMING"&&running){pill.textContent="AUTO-RESUMING";}
}
function renderTraining(info){
 trainingStatus=info;
 renderModelIdentity();
 const run=info.run||{};
 const running=!!info.running;
 const hasActive=!!info.current_model?.promoted_hash;
 const prospective=hasActive?(info.continuation||{}):(info.forward_480||{});
 const baseline=info.baseline_retrain||{};
 const ready=!!(info.train_available ?? (info.ready||prospective.ready||baseline.ready));
 const mode=String(info.selected_mode||
  (info.ready?info.mode:baseline.ready?"baseline":prospective.ready?"forward_480":""));
 const phase=String(run.stage||"");
 const pill=el("train-status-pill");
 pill.textContent=info.pause_requested&&running?"PAUSE_REQUESTED":
  info.paused||(!running&&run.stage==="PAUSED")?"PAUSED":
  running?"TRAINING RUNNING":ready?"READY TO TRAIN":
  info.status==="BLOCKED"?"DATA BLOCKED":"COLLECTING DATA";
 pill.className="training-pill"+(running||ready?"":" waiting");
 const n=Math.max(0,Number(prospective.eligible_markets??prospective.eligible_480_markets)||0);
 const target=Math.max(1,Number(prospective.required_markets)||120);
 const p=Math.max(0,Math.min(100,Number(prospective.progress_percent)||0));
 text("forward-480-progress",p+"%");
 el("forward-480-bar-fill").style.width=p+"%";
 text("forward-480-eligible",n+" / "+target+" verified markets · "+
  String(prospective.verified_outcome_classes||0)+" / 2 settled outcome classes");
 text("forward-480-reason",prospective.reason||"Waiting for verified forward 480s features.");
 text("baseline-retrain-ready",hasActive?
  "Full-model rebuild disabled for the primary Train Now workflow. Existing fitted classifier weights are continued without rebuilding the historical dataset.":
  (baseline.ready?"Full model ready: ":"Full model: ")+
  (baseline.reason||"Checking historical dataset and fresh independent lockbox"));
 text("training-headline",run.stage==="PAUSED"?"Training paused":
   run.stage==="FAILED"?"Previous training stopped — model unchanged":
  info.pause_requested?"Pausing after the next safe checkpoint…":
  running?"Training and evaluating…":
  mode==="continue_live"?"Ready to continue active Model #"+String(info.current_model?.model_number||"?"):
  hasActive?"Collecting new settlements to continue Model #"+String(info.current_model?.model_number||"?"):
  mode==="forward_480"?"Ready to train the 480-second research challenger":
  mode==="baseline"?"Ready to train and evaluate a full model":
  mode==="mistake"?"Ready to learn from verified mistakes":
  mode==="correct"?"Ready for verified correct-case refresher":
  ready?"Ready for verified training":"Waiting for verified training evidence");
 text("training-description",running?
  "The worker is fitting and evaluating with real checkpoint data. Live predictions and trading remain unchanged until an authorized, compatible model is promoted.":
  hasActive?
  "Continue the current model's existing fitted logistic and boosting classifiers on newly settled 480s markets. Keep structural forecasts, stacking and calibration; no full retrain. Promote only after independent validation of the configured training policy. "+String(prospective.reason||""):
  mode==="forward_480"?
  "Train on a newly sealed 480s snapshot dataset, test on later independent markets, and report whether accuracy and coverage improved. Research specialists cannot yet replace the full live pipeline.":
  mode==="baseline"?
  "Build and audit the dataset; fit a compatible new predictor; score a fresh lockbox; compare against the running model at eight minutes; promote only if all gates pass.":
  mode==="mistake"?
  "Train on verified earlier model mistakes, then independently evaluate and promote only if the safeguards pass.":
  mode==="correct"?
  "Refresh using verified correct-case examples, then independently evaluate and promote only if the safeguards pass.":
  "Neither training path currently has all of its verified prerequisites.");
 const button=el("train-now");
 button.disabled=!ready||running||phase==="STARTING";
 button.textContent=running?"Continuing…":hasActive?"Continue Training":"Train Now";
 updateTrainingPauseControls(info);
 renderTrainingSession(run,running);
 if(info.pause_requested&&running){
  text("train-feedback","Pause requested · waiting for the current market or atomic model stage.");
 }else if(info.paused||(!running&&run.stage==="PAUSED")){
  text("train-feedback","Paused at "+String(run.resume_stage||"saved checkpoint").replaceAll("_"," ")+" · Resume continues the same session.");
 }else if(running){
  text("train-feedback","Worker active · "+String(run.mode||mode).replaceAll("_"," ")+
   " · "+(run.updated_utc||"awaiting first milestone"));
 }else if(run.stage==="COMPLETE"){
  text("train-feedback","Training run finished · "+String(run.decision||"REVIEW RESULTS").replaceAll("_"," "));
 }else if(run.stage==="FAILED"||run.stage==="INTERRUPTED"){
  text("train-feedback","Training stopped · "+(run.error||"See diagnostic report"));
 }else{
  text("train-feedback",ready?"Ready. Press Train Now to begin a recorded training session.":
   "The progress shown above is evidence readiness, not training time.");
 }
 // An HTTP 409/403/500 must remain visible through the subsequent status GET
 // and scheduled refreshes instead of making Resume appear to do nothing.
 if(trainingActionError)text("train-feedback",trainingActionError);
}
async function refreshTraining(){
 try{
  const response=await fetch("/api/training",{cache:"no-store"});
  if(!response.ok)throw Error("HTTP "+response.status);
  const info=await response.json();
  renderTraining(info);
  saveTrainingSnapshot(info);
 }catch(err){
  if(trainingStatus){
   // Keep completed stages, progress, model identity and the latest verdict.
   el("train-now").disabled=true;
   text("train-feedback","Connection interrupted · showing last verified training status; "+
    "retrying soon. "+err.message);
   return;
  }
  text("training-headline","Training readiness unavailable");
  text("train-status-pill","CONNECTION ERROR");
  el("train-now").disabled=true;
  text("forward-480-eligible","Forward training status unavailable");
  text("forward-480-progress","—");
  text("forward-480-reason","Cannot load verified prospective evidence.");
  text("training-outcome","The training service is unavailable: "+err.message);
 }
}
// Worker-only progress poll: no feature rebuild, model scoring or expensive
// readiness inspection. Full training status still syncs every five seconds.
let progressPollPending=false;
async function refreshTrainingProgress(){
 if(progressPollPending||document.hidden||!trainingStatus||!trainingStatus.running)return;
 progressPollPending=true;
 try{
  const response=await fetch("/api/training/progress",{cache:"no-store"});
  if(!response.ok)throw Error("HTTP "+response.status);
  const payload=await response.json();
  if(!payload||typeof payload!=="object")return;
  const next=payload.run;
  if(!next||typeof next!=="object")return;
  const previous=trainingStatus.run||{};
  const newer=String(next.updated_utc||"");
  const older=String(previous.updated_utc||"");
  // A late GET must never move the same in-progress dataset back.
  if(next.started_utc===previous.started_utc&&newer&&older&&newer<older)return;
  const running=payload.running===true;
  if(newer===older&&next.stage===previous.stage&&running===trainingStatus.running
     &&payload.pause_requested===trainingStatus.pause_requested
     &&payload.paused===trainingStatus.paused)return;
  trainingStatus={...trainingStatus,...payload,run:next,running};
  renderTrainingSession(next,running);
  updateTrainingPauseControls(trainingStatus);
  saveTrainingSnapshot(trainingStatus);
  if(running){
   text("train-status-pill",trainingStatus.pause_requested?"PAUSE_REQUESTED":
    next.stage==="RESUMING"?"AUTO-RESUMING":"TRAINING RUNNING");
   text("training-headline",trainingStatus.pause_requested?
    "Pausing after the next safe checkpoint…":"Training and evaluating…");
   text("train-feedback","Worker active · "+String(next.stage||"STARTING").replaceAll("_"," ")+
    " · "+(next.updated_utc||"checking latest state"));
   el("train-now").disabled=true;
  }else{
   el("train-now").disabled=true;
   refreshTraining();
  }
 }catch(err){
  // Temporary network delay leaves last verified progress visible.
 }finally{
  progressPollPending=false;
 }
}
async function trainingControl(action){
 trainingActionError="";
 const button=el(action==="pause"?"train-pause":"train-resume");
 button.disabled=true;
 text("train-feedback",action==="pause"?
  "Requesting a safe pause after the current market or model stage…":
  "Resuming saved training session…");
 try{
  const endpoint=action==="pause"?"/api/training/pause":"/api/training/resume";
  const response=await fetch(endpoint,{
   method:"POST",headers:{"Content-Type":"application/json",
    "X-KalshiEdge-Action":"train"},body:"{}",cache:"no-store",
  });
  const result=await response.json();
  if(!response.ok)throw Error(result.error||"HTTP "+response.status);
  if(action==="pause"&&result.paused===true){
   // Only claim PAUSED when the operating system froze the fit worker.
   const previous=trainingStatus||{};
   const run=previous.run||{};
   trainingStatus={...previous,paused:true,pause_requested:true,running:false,
    run:{...run,stage:"PAUSED",resume_stage:run.stage||"TRAINING",
     suspended_in_memory:true}};
   renderTrainingSession(trainingStatus.run,false);
   updateTrainingPauseControls(trainingStatus);
   text("training-headline","Model fitting paused instantly");
   text("train-feedback","Paused immediately · same model fit is preserved in memory "+
    "across KalshiEdge closing and reopening.");
   saveTrainingSnapshot(trainingStatus);
   return; // no expensive full readiness scan required to acknowledge pause
  }
  if(action==="resume"&&result.resumed===true&&result.in_memory===true){
   const previous=trainingStatus||{};
   const run=previous.run||{};
   trainingStatus={...previous,paused:false,pause_requested:false,running:true,
    run:{...run,stage:run.resume_stage||"TRAINING",suspended_in_memory:false}};
   renderTrainingSession(trainingStatus.run,true);
   updateTrainingPauseControls(trainingStatus);
   text("training-headline","Training and evaluating…");
   text("train-feedback","Resumed the same in-memory model fit.");
   saveTrainingSnapshot(trainingStatus);
   return;
  }
  text("train-feedback",action==="pause"?
   "Pause requested · waiting for the worker's safe checkpoint.":
   "Saved session resumed. Verified dataset checkpoints and fitted artifacts will be reused.");
 }catch(err){
  trainingActionError="Could not "+action+" training: "+err.message;
 }
 await refreshTraining();
 if(trainingActionError)text("train-feedback",trainingActionError);
}
el("train-pause").addEventListener("click",()=>trainingControl("pause"));
el("train-resume").addEventListener("click",()=>trainingControl("resume"));
el("train-now").addEventListener("click",async()=>{
 if(!(trainingStatus?.train_available??(trainingStatus?.ready||
     trainingStatus?.baseline_retrain?.ready||trainingStatus?.forward_480?.ready))||
    trainingStatus?.running)return;
 const mode=String(trainingStatus?.selected_mode||
  (trainingStatus?.ready?trainingStatus?.mode:
   trainingStatus?.baseline_retrain?.ready?"baseline":"forward_480"));
 const prompt=mode==="continue_live"?
  "Continue the CURRENT active model's fitted classifier weights? Only NEW verified settled 480-second markets will be used. KalshiEdge will preserve the structural, stacking and calibration components, compare the continuation against the current model at >69% predicted-direction probability on later markets, and retain a checkpoint if not promoted. No full retrain or automatic trades are performed.":
  mode==="forward_480"?
  "Start verified 480-second training? This creates and audits an immutable dataset, trains a model, compares it against the current model on later markets, and reports exact pass/fail reasons. Research specialists cannot replace the full live pipeline without prospective validation.":
  mode==="baseline"?
  "Start full-model training? KalshiEdge will build and audit the dataset, train a compatible predictor, and compare it against the current model on independent eight-minute lockbox markets. Only one that improves accuracy and coverage with stable probability quality can be promoted; restart is required.":
  "Start the verified "+String(trainingStatus?.mode_label||"historical")+" cycle? Only independently passing, compatible models can be promoted for the next restart.";
 if(!confirm(prompt))return;
 const button=el("train-now");
 button.disabled=true;
 text("train-feedback","Starting the recorded training worker…");
 try{
  const response=await fetch("/api/training/start",{
   method:"POST",headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"train"},
   body:"{}",cache:"no-store",
  });
  const result=await response.json();
  if(!response.ok)throw Error(result.error||"HTTP "+response.status);
  text("train-feedback","Training worker started · "+String(result.mode).replaceAll("_"," ")+
   " · tracking progress below.");
 }catch(err){
  text("train-feedback","Could not start training: "+err.message);
 }
 await refreshTraining();
});
refreshLive();refreshHistory();refreshTraining();
setInterval(refreshLive,750);setInterval(refreshHistory,5000);setInterval(refreshTraining,5000);setInterval(clock,250);
</script>
</body></html>
"""
