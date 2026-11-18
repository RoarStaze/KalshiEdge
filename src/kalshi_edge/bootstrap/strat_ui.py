"""Standalone Strat experiment page; isolated from live Trade/AutoTrade DOM."""
from __future__ import annotations


def render_strat_page() -> str:
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#07131a"><title>KalshiEdge · Strat</title>
<style>
:root{color-scheme:dark;font-family:Segoe UI,Inter,system-ui,sans-serif;
--fg:#ebfaf7;--muted:#98b2b5;--accent:#44e4b6;--border:#236258}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:
linear-gradient(165deg,#06161b,#071014 75%);color:var(--fg)}
body:before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
background:linear-gradient(#06161be6,#06151af2),
url("/assets/kalshiedge-neon-city.webp") center/cover no-repeat}
main{max-width:1480px;margin:auto;padding:22px 28px 60px}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;
border-bottom:1px solid #ffffff17;padding:8px 0 22px}
.market{font-size:21px;font-weight:850;letter-spacing:2px}
nav{display:flex;flex-wrap:wrap;gap:4px;padding:5px;background:#0e2327;border-radius:15px;
border:1px solid var(--border)}
nav a{color:var(--muted);text-decoration:none;padding:11px 17px;border-radius:10px;
font-weight:700;font-size:13px}
nav a.current{background:#245246;color:var(--accent)}
small,.sub{color:var(--muted)}
h1{font-size:clamp(27px,3vw,42px);letter-spacing:-1.2px;margin:24px 0 6px}
.sub{font-size:13px;line-height:1.65;margin:0 0 22px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.card{background:linear-gradient(150deg,#0c2529ee,#071820f4);
border:1px solid #2c665c;border-radius:18px;padding:22px;
box-shadow:0 10px 35px #0007,0 0 18px #19bb9512;min-width:0}
.card h2{font-size:13px;letter-spacing:1.4px;text-transform:uppercase;color:#8ce0c3;
font-weight:800;margin:0 0 21px}
.metric{font-size:35px;font-weight:760;font-variant-numeric:tabular-nums;letter-spacing:-1px}
.caption{font-size:11px;color:var(--muted);margin:5px 0 19px}
.kv{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #ffffff13;
padding:12px 0;font-size:13px}
.kv>span:first-child{color:var(--muted)}
.positive{color:#52e5a8}.negative{color:#ff8394}
label{display:block;font-size:12px;color:#a6c5bf;margin-bottom:8px}
input{width:100%;background:#08181c;border:1px solid #3b7068;border-radius:10px;
padding:12px;color:var(--fg);font:inherit}
button{border:1px solid #4eab91;border-radius:11px;background:#164b3f;color:#e4fff4;
font-weight:700;padding:13px 18px;cursor:pointer}
button:disabled{opacity:.45;cursor:not-allowed}
button.secondary{background:#28302e;border-color:#737b73}
.actions{display:flex;gap:10px;margin-top:15px;flex-wrap:wrap}
.status{font-size:12px;overflow-wrap:anywhere;line-height:1.5;margin:14px 0 0;color:#c0e1d7}
.span{grid-column:1/-1}
.rules{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px}
.rule{border:1px solid #214c48;border-radius:11px;padding:13px;background:#091c20}
.rule strong{display:block;font-size:20px;color:#d8fff0;margin:6px 0}
.rule small{font-size:11px}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:12px 8px;text-align:left;border-top:1px solid #ffffff17}
th{color:#a6c5bf;font-size:11px}
td{font-variant-numeric:tabular-nums}
.scroll{overflow:auto}
.badge{font-size:11px;border:1px solid #407a67;padding:5px 9px;
border-radius:12px;color:#8ae6bd}
@media(max-width:1060px){.grid{grid-template-columns:1fr 1fr}.rules{grid-template-columns:1fr 1fr}}
@media(max-width:700px){main{padding:14px}header{flex-direction:column;align-items:stretch}
nav{justify-content:center}.grid{grid-template-columns:1fr}.rules{grid-template-columns:1fr 1fr}}
</style></head>
<body><main><header><div class="market">KALSHIEDGE <span class="badge">EXPERIMENTAL</span></div>
<nav aria-label="Primary navigation">
<a href="/trade">Trade</a><a href="/train">Train</a>
<a href="/history">History</a><a href="/strat" class="current" aria-current="page">Strat</a>
<a href="/api">API</a></nav></header>
<h1>UP Momentum · Strat</h1>
<p class="sub">15-minute BTC · repeated UP entries and momentum-slowdown exits ·
<strong>PAPER SIMULATION ONLY</strong>. No real orders are submitted.</p>
<div class="grid">
<section class="card"><h2>Current Market</h2>
<div id="remain" class="metric">—</div><div class="caption">Time until market close</div>
<div class="kv"><span>Market</span><span id="ticker">Waiting for feed</span></div>
<div class="kv"><span>Executable UP ask</span><strong id="upAsk">—</strong></div>
<div class="kv"><span>Entry window</span><span id="window">—</span></div>
<div class="kv"><span>Sample count</span><span id="samples">0 / 5</span></div>
</section>
<section class="card"><h2>Live Momentum</h2>
<div id="speed" class="metric">—</div><div class="caption">UP bid change · cents per second</div>
<div class="kv"><span>Acceleration ratio</span><strong id="ratio">—</strong></div>
<div class="kv"><span>Decision</span><strong id="signal">DISARMED</strong></div>
<div class="kv"><span>Paper position</span><span id="held">FLAT</span></div>
<div class="kv"><span>Round trips this market</span><strong id="cycles">0</strong></div>
</section>
<section class="card"><h2>Experiment Controls</h2>
<div class="kv"><span>Mode</span><strong class="positive">PAPER ONLY</strong></div>
<div class="kv"><span>State</span><strong id="enabled">Disabled</strong></div>
<label for="amount">Maximum simulated budget per entry (USD)</label>
<input id="amount" type="number" min="0.10" max="5" step="0.01" value="1.00">
<div class="actions"><button id="arm" type="button">Arm paper Strat</button>
<button id="pause" class="secondary" type="button">Pause entries</button></div>
<p class="status" id="feedback" role="status">Arming is required after each restart. Existing simulated holdings remain monitored when paused.</p>
<div class="kv"><span>Simulated P&amp;L</span><strong id="pnl">—</strong></div>
<div class="kv"><span>Virtual cash</span><span id="cash">—</span></div>
</section>
<section class="card span"><h2>Exactly four strategy rules</h2><div class="rules">
<div class="rule"><small>BUY TIME</small><strong>≥ 7:00</strong><small>Never open a new UP position below seven minutes remaining.</small></div>
<div class="rule"><small>UP PRICE</small><strong>65¢–85¢</strong><small>Entry is based on the executable UP ask, not the last trade.</small></div>
<div class="rule"><small>BUY TRIGGER</small><strong>UP speed ↑</strong><small>Fresh simultaneous bid/ask burst, faster than the preceding samples.</small></div>
<div class="rule"><small>SELL TRIGGER</small><strong>UP speed ↓</strong><small>Bid momentum slows materially or reverses. Exits remain allowed below 7:00.</small></div>
</div></section>
<section class="card span"><h2>Experimental round trips · persistent local ledger</h2>
<div class="scroll"><table><thead><tr><th>Market</th><th>State</th><th>Entry</th><th>Exit</th><th>Shares</th><th>Net hypothetical P&amp;L</th></tr></thead>
<tbody id="rounds"><tr><td colspan="6">No simulated rounds yet</td></tr></tbody></table></div>
<p class="sub">Simulated entry/exit includes one cent slippage on each leg and fee estimates. Paper fills are not evidence of fillability or profitability.</p>
</section></div></main>
<script>
"use strict";
const el=id=>document.getElementById(id);
const set=(id,value)=>{el(id).textContent=String(value??"—")};
const money=v=>v==null?"—":"$"+Number(v).toFixed(4);
function row(table,values){const tr=document.createElement("tr");
values.forEach(v=>{const td=document.createElement("td");td.textContent=String(v??"—");tr.appendChild(td)});
table.appendChild(tr)}
async function refresh(){
 try{
  const [s,l]=await Promise.all([
   fetch("/api/strat/status",{cache:"no-store"}).then(r=>{if(!r.ok)throw Error("Strat API offline");return r.json()}),
   fetch("/api/live",{cache:"no-store"}).then(r=>r.json())]);
  set("enabled",s.enabled?"ARMED · PAPER":"DISABLED");
  set("signal",s.last_signal);set("samples",s.samples+" / 5");
  set("speed",Number(s.speed_cents_per_second).toFixed(2)+" ¢/s");
  set("ratio",s.acceleration_ratio==null?"—":Number(s.acceleration_ratio).toFixed(2)+"×");
  set("pnl",money(s.paper_net_pnl_dollars));set("cash",money(s.virtual_cash_dollars));
  set("held",s.open_position?"LONG UP · "+s.open_position.quantity+" shares":"FLAT");
  const ticker=l.market_ticker||s.market_ticker;set("ticker",ticker||"Waiting");
  const sec=Number(l.seconds_remaining);const valid=Number.isFinite(sec)&&sec>0;
  set("remain",valid?Math.floor(sec/60)+":"+String(Math.floor(sec%60)).padStart(2,"0"):"—");
  set("window",valid?(sec>=420?"7-minute window open":"NEW BUYS CLOSED"):"WAIT");
  const b=s.round_trips||[];set("cycles",b.filter(r=>r.ticker===ticker).length);
  const tb=el("rounds");tb.replaceChildren();
  if(!b.length){row(tb,["No simulated rounds yet","","","","",""]);}else{
   b.forEach(r=>row(tb,[r.ticker,r.status,Number(r.entry_price)*100+"¢",
     r.exit_price==null?"—":Number(r.exit_price)*100+"¢",
     r.quantity,r.paper_net_pnl==null?"OPEN":money(r.paper_net_pnl)]))}
  el("arm").disabled=s.enabled;el("pause").disabled=!s.enabled;
  set("upAsk",s.up_ask_cents==null?"—":s.up_ask_cents+"¢");
 }catch(e){set("feedback","Status unavailable: "+e.message)}
}
async function action(kind){
 el("arm").disabled=true;el("pause").disabled=true;
 try{
  const payload=kind==="arm"?{amount:Number(el("amount").value).toFixed(2)}:{};
  const r=await fetch("/api/strat/"+kind,{
   method:"POST",headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"strat"},
   body:JSON.stringify(payload)});
  const data=await r.json();if(!r.ok)throw Error(data.error||"Action rejected");
  set("feedback",kind==="arm"?"Paper Strat armed · no real orders":"Entries paused; any open paper position remains monitored");
  await refresh();
 }catch(e){set("feedback",e.message);await refresh()}
}
el("arm").addEventListener("click",()=>action("arm"));
el("pause").addEventListener("click",()=>action("pause"));
refresh();setInterval(refresh,900);
</script></body></html>"""
