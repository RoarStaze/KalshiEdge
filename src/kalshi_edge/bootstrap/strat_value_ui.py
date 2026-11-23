"""The Strat V2 settlement-value paper research page."""
from __future__ import annotations


def render_value_strat_page() -> str:
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KalshiEdge · Strat V2</title><style>
:root{color-scheme:dark;--fg:#e7fbf7;--muted:#a1c3be;--mint:#58ecc0;--edge:#326b62;--warn:#e9c78e;font-family:Inter,Segoe UI,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 70% -20%,#17483e,transparent 60%),#08161b;color:var(--fg)}
main{max-width:1240px;margin:0 auto;padding:22px 20px 56px}nav{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:25px}
nav a{color:var(--muted);text-decoration:none;border:1px solid var(--edge);padding:9px 15px;border-radius:9px;font-size:13px}
nav a.active{color:var(--mint);border-color:var(--mint)}
h1{font-size:clamp(26px,3.3vw,38px);margin:7px 0}h2{font-size:12px;letter-spacing:1.4px;text-transform:uppercase;color:var(--mint);margin:0 0 16px}
.note{color:var(--muted);line-height:1.65;font-size:12px}.note strong{color:var(--warn)}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:21px}
.card{background:linear-gradient(135deg,#132a2e,#0d1f25);padding:22px;border-radius:16px;border:1px solid var(--edge);min-width:0}
.card.full{grid-column:1/-1}.big{font-size:clamp(23px,3.2vw,34px);font-weight:750;overflow-wrap:anywhere}
.kv{display:flex;justify-content:space-between;gap:12px;border-top:1px solid #2c4849;padding:10px 0;font-size:12px}
.kv span:first-child{color:var(--muted)}.kv strong{font-variant-numeric:tabular-nums;text-align:right;overflow-wrap:anywhere}
label{display:block;margin:13px 0 7px;font-size:12px;color:var(--muted)}
input{background:#061a1c;color:var(--fg);padding:11px;border-radius:9px;border:1px solid var(--edge);width:100%}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
button{background:#145547;border:1px solid var(--mint);color:var(--fg);cursor:pointer;padding:10px 15px;border-radius:9px;font-weight:700}
button.secondary{background:#263839;border-color:#69827c}button:disabled{opacity:.5}
.green{color:var(--mint)}.warn{color:var(--warn)}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:750px;font-size:11px}
th,td{padding:12px 9px;text-align:left;border-bottom:1px solid #2c494a;white-space:nowrap}
th{color:var(--muted)}
@media(max-width:850px){.grid{grid-template-columns:1fr}.card{padding:16px}}
</style></head><body><main>
<nav><a href="/trade">Trade</a><a href="/train">Train</a><a href="/history">History</a><a href="/api">API</a><a class="active" href="/strat">Strat</a></nav>
<h1>Settlement Value · Strat V2</h1>
<p class="note">15-minute BTC · Frozen forecast at 8 minutes remaining · fee-aware order-book value, one entry per market, held to locally integrity-checked Kalshi API results (Verified settlement record; independent BRTI reconstruction not yet certified). <strong>PAPER ONLY. No verified profitable edge.</strong></p>
<div class="grid">
<section class="card"><h2>Current Market</h2><div id="timer" class="big">—</div><p class="note">Time until market closes</p>
<div class="kv"><span>Market</span><strong id="ticker">Waiting</strong></div>
<div class="kv"><span>Frozen forecast (480s)</span><strong id="direction">—</strong></div>
<div class="kv"><span>Original model probability</span><strong id="modelP">—</strong></div>
<div class="kv"><span>Conservative probability</span><strong id="guardedP">—</strong></div>
<div class="kv"><span>Near-strike risk</span><strong id="coinflip">—</strong></div>
<p class="note">A qualifying trade must occur within 30 seconds after the sealed 8-minute checkpoint. Missing/stale forecasts fail closed.</p></section>
<section class="card"><h2>Expected Value · Not Verified</h2>
<div id="signal" class="big">WAITING</div>
<p class="note">No verified profitable edge · expected net value is a model-dependent estimate, not realized profit.</p>
<div class="kv"><span>Executable ask</span><strong id="ask">—</strong></div>
<div class="kv"><span>Modeled entry incl. 1¢ slippage</span><strong id="entryPrice">—</strong></div>
<div class="kv"><span>Conservative fee reserve / contract</span><strong id="fees">—</strong></div>
<div class="kv"><span>Expected net value after safety buffer</span><strong id="edge">—</strong></div>
<div class="kv"><span>Decision reason</span><strong id="reason">Waiting</strong></div>
<p class="note">25% shrink toward 50% + additional 5¢ per-contract buffer + 1.5× standard taker-fee allowance are conservative <strong>heuristics</strong>, not calibrated proof.</p>
</section>
<section class="card"><h2>Paper Controls &amp; Risk</h2>
<div class="kv"><span>Execution</span><strong class="green">PAPER ONLY</strong></div>
<div class="kv"><span>Status</span><strong id="armed">Disabled</strong></div>
<label for="amount">Maximum hypothetical stake per entry (USD)</label>
<input id="amount" type="number" min="0.10" max="2" step=".01" value="1.00">
<div class="actions"><button id="arm" type="button">Arm value Strat</button><button class="secondary" id="pause" type="button">Pause new entries</button></div>
<p class="note" id="feedback" role="status">Restarting always disables entries. Holdings remain monitored for official settlement.</p>
<div class="kv"><span>Risk limit</span><strong>≤1% virtual cash/position, $2 daily loss</strong></div>
<div class="kv"><span>Settled wins / all settled</span><strong id="settled">—</strong></div>
<div class="kv"><span>Simulated net P&amp;L</span><strong id="pnl">—</strong></div>
<div class="kv"><span>Virtual cash</span><strong id="cash">—</strong></div>
<div class="kv"><span>Holding</span><strong id="holding">FLAT</strong></div></section>
<section class="card full"><h2>What Changed · Research Status</h2>
<p class="note"><strong>Legacy momentum chasing is not the active strategy.</strong> This V2 uses the immutable KalshiEdge eight-minute model forecast and enters only when the selected UP/DOWN executable ask, fees, spread, depth and conservative probability pass a model-value gate. No repeated 2-second scalp, no assumed maker fill, no profit-only exit. Verified settlement determines the result.</p>
<p class="note"><strong>No verified profitable edge.</strong> Simulated taker fills and model probabilities can be wrong. Zero trades is correct when markets are efficiently priced, the model is unverified, or quotes are stale. Old Legacy momentum trades remain untouched in data/bootstrap/trading/strat_up_paper.sqlite3 and are never blended into V2 performance.</p>
</section>
<section class="card full"><h2>V2 Verified Settlement Ledger</h2>
<div class="scroll"><table><thead><tr><th>Market</th><th>Side</th><th>State</th><th>Entry</th><th>Quantity</th><th>Entry fee</th><th>Settlement</th><th>Paper net P&amp;L</th></tr></thead>
<tbody id="rows"><tr><td colspan="8">No V2 paper trades yet</td></tr></tbody></table></div>
<p class="note">An OPEN position has no claimed outcome until the official result is independently verified. Legacy momentum positions remain handled separately.</p>
</section></div></main>
<script>
"use strict";
const el=id=>document.getElementById(id);
const set=(id,value)=>{el(id).textContent=String(value==null?"—":value)};
const cents=v=>v==null?"—":(100*Number(v)).toFixed(2)+"¢";
const money=v=>v==null?"—":"$"+Number(v).toFixed(4);
const pct=v=>v==null?"—":(100*Number(v)).toFixed(2)+"%";
const label=v=>String(v||"WAITING").replace(/_/g," ");
function show(s,l){
 set("armed",s.enabled?"ARMED · PAPER":"DISABLED");
 set("ticker",l.market_ticker||s.market_ticker||"Waiting");
 const sec=Number(l.seconds_remaining);
 set("timer",Number.isFinite(sec)&&sec>0?Math.floor(sec/60)+":"+String(Math.floor(sec%60)).padStart(2,"0"):"—");
 const f=l.final_forecast||{};
 set("direction",f.forecast||"Waiting for frozen checkpoint");
 set("coinflip",f.coin_flip_assessment||"Unknown");
 const v=s.signal||{},q=s.quote||{};
 set("modelP",pct(v.model_probability??f.directional_probability));
 set("guardedP",pct(v.guarded_probability));
 set("ask",cents(v.ask??q.ask));
 set("entryPrice",cents(v.effective_buy_price));
 set("fees",cents(v.fee_reserve_per_share));
 set("edge",cents(v.estimated_edge_per_share));
 set("signal",label(s.last_signal));set("reason",label(v.reason||s.last_signal));
 set("settled",String(s.settled_winners??0)+" / "+String(s.settled_count??0));
 set("pnl",money(s.paper_net_pnl_dollars));
 set("cash",money(s.virtual_cash_dollars));
 const p=s.open_position;
 set("holding",p?p.side+" "+p.quantity+" shares · AWAIT SETTLEMENT":"FLAT");
 const body=el("rows");body.replaceChildren();
 if(!(s.round_trips||[]).length){
  const tr=document.createElement("tr"),td=document.createElement("td");
  td.colSpan=8;td.textContent="No V2 paper positions";tr.appendChild(td);body.appendChild(tr);
 }
 for(const r of s.round_trips||[]){
  const tr=document.createElement("tr");
  const data=[r.ticker,r.side,r.status,cents(r.entry_price),r.quantity,
   money(r.entry_fee),r.exit_reason||"Awaiting verified settlement",
   r.paper_net_pnl==null?"—":money(r.paper_net_pnl)];
  for(const value of data){const td=document.createElement("td");td.textContent=value;tr.appendChild(td)}
  body.appendChild(tr);
 }
}
async function refresh(){
 try{
  const [s,l]=await Promise.all([
   fetch("/api/strat/status",{cache:"no-store"}).then(r=>{if(!r.ok)throw Error("Strat API offline");return r.json()}),
   fetch("/api/live",{cache:"no-store"}).then(r=>r.json())
  ]);
  show(s,l);
 }catch(e){set("feedback","Data unavailable: "+e.message)}
}
async function action(which){
 const button=el(which);button.disabled=true;
 try{
  const amount=Number(el("amount").value);
  if(which==="arm"&&!(amount>=.10&&amount<=2))throw Error("Budget must be $0.10–$2.00");
  const payload=which==="arm"?{amount:amount.toFixed(2)}:{};
  const endpoint=which==="arm"?"/api/strat/arm":"/api/strat/pause";
  const r=await fetch(endpoint,{method:"POST",credentials:"same-origin",
   headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"strat"},
   body:JSON.stringify(payload)});
  const s=await r.json();if(!r.ok)throw Error(s.error||"Request rejected");
  set("feedback",which==="arm"?"Armed, paper only. Model and value filters are enforced.":"Paused new entries; open holdings still await settlement.");
 }catch(e){set("feedback",e.message)}
 finally{button.disabled=false;refresh()}
}
el("arm").addEventListener("click",()=>action("arm"));
el("pause").addEventListener("click",()=>action("pause"));
refresh();setInterval(refresh,1500);
</script></body></html>"""
