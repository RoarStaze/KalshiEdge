"""Manual Kalshi buy/sell card, styled to the user's reference screenshots.

Preserves original dashboard grid and forecast card size. Original snapshot and
health elements stay in the DOM but are hidden so forecast JS still updates.
"""
from __future__ import annotations

STYLE = r"""
/* Manual trading replaces the two informational cards, not the forecast. */
.context>.context-header,.context>.health{display:none!important}
.context>.manual-trade{display:block;box-sizing:border-box;width:100%;max-width:none;min-height:555px;
  flex:1;border:1px solid #2a2d34;background:#101116;box-shadow:none;color:#e9e9eb;
  font-family:system-ui,-apple-system,Segoe UI,sans-serif;border-radius:19px;overflow:hidden}
.manual-trade *{box-sizing:border-box}
.manual-trade .mt-top{height:68px;display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;
  border-bottom:1px solid #26282e;padding:0 20px}
.manual-trade .mt-tabs{display:flex;gap:18px;justify-self:start}
.manual-trade .mt-account-summary{display:flex;align-items:center;justify-content:center;gap:19px;min-width:0}
.manual-trade .mt-account-item{display:flex;align-items:center;flex-direction:column;gap:4px;white-space:nowrap}
.manual-trade .mt-account-value{font-size:12px;font-weight:720;line-height:1.15;font-variant-numeric:tabular-nums}
.manual-trade .mt-account-label{font-size:11px;font-weight:550;line-height:1.2}
.manual-trade .mt-account-item.portfolio{color:#20d6a0}
.manual-trade .mt-tab{font-size:14px;font-weight:850;letter-spacing:2px;color:#67666c;background:none;
  border:0;cursor:pointer;padding:23px 0}
.manual-trade .mt-tab.active{color:#f4f4f4}
.manual-trade .mt-units{background:transparent;color:#f1f1f4;border:0;font-weight:850;font-size:12px;
  letter-spacing:2px;cursor:pointer;padding:10px;justify-self:end}
.manual-trade .mt-main{padding:22px 20px 25px}
.manual-trade .mt-title{display:flex;gap:12px;align-items:center;font-size:clamp(15px,1.5vw,23px);
  font-weight:850;letter-spacing:-.5px;line-height:1.3;margin-bottom:22px}
.manual-trade .mt-btc{display:grid;place-items:center;flex:none;width:36px;height:36px;border-radius:8px;
  background:#f89a3d;color:#fff;font-size:23px;font-weight:800}
.manual-trade .mt-pills{display:flex;width:min(100%,365px);border-radius:24px;overflow:hidden;
  background:#26272b;border:1px solid #35363b;margin-bottom:20px}
.manual-trade .mt-pill{flex:1;min-width:0;border:0;background:transparent;color:#f0f0f0;padding:10px 7px;
  border-radius:24px;font-size:14px;font-weight:850;letter-spacing:1px;cursor:pointer}
.manual-trade .mt-pill.mt-up.active{background:#25bf85;color:#071e16}
.manual-trade .mt-pill.mt-down.active{background:#ff4458;color:#09090b}
.manual-trade .mt-pill.mt-auto.active{background:linear-gradient(135deg,#e9bf55,#f6d889);color:#231902;box-shadow:inset 0 0 0 1px #ffe6a5}
.manual-trade .mt-pill.mt-auto:focus-visible{outline:2px solid #f6d889;outline-offset:-3px}
.manual-trade .mt-auto-arm{display:block;width:100%;min-height:50px;margin-top:12px;border-radius:26px;
 background:linear-gradient(120deg,#e9bf55,#f9d984);color:#291d06;border:1px solid #fce6a9;
 font-size:14px;font-weight:850;cursor:pointer;box-shadow:0 8px 22px #e9bf5522;transition:filter .2s}
.manual-trade .mt-auto-arm:hover:not(:disabled){filter:brightness(1.08)}
.manual-trade .mt-auto-arm:disabled{opacity:.45;cursor:not-allowed}
.manual-trade .mt-auto-arm.armed{background:#3a3020;color:#f6d889;border-color:#a18748}
.manual-trade .mt-auto-status{font-size:12px;line-height:1.6;margin-top:9px;color:#f1cc70;min-height:19px}
.manual-trade .mt-auto-history{margin-top:22px;border-top:1px solid #49433a;padding-top:17px}
.manual-trade .mt-auto-history-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;font-size:12px;color:#ebd18f;font-weight:800}
.manual-trade .mt-auto-history-list{display:grid;gap:9px;max-height:260px;overflow-y:auto;scrollbar-width:thin}
.manual-trade .mt-auto-history-count,.manual-trade .mt-auto-record-sub{font-size:11px;color:#9daeb0;font-weight:400}
.manual-trade .mt-auto-record{border:1px solid #353e41;border-radius:11px;background:#152024;padding:11px;display:grid;gap:6px}
.manual-trade .mt-auto-record-top,.manual-trade .mt-auto-record-sub{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.manual-trade .mt-auto-record-top{font-size:13px;font-weight:800}
.manual-trade .mt-auto-record-side.up,.manual-trade .mt-auto-record-status.filled{color:#32dba8}
.manual-trade .mt-auto-record-side.down{color:#ff738f}
.manual-trade .mt-auto-record-status.warn{color:#f1c46b}
.manual-trade .mt-auto-pnl-row{display:flex;align-items:baseline;justify-content:flex-end;gap:12px;flex-wrap:wrap;font-size:12px;font-variant-numeric:tabular-nums}
.manual-trade .mt-auto-pnl-gain{color:#29d599;font-weight:800}
.manual-trade .mt-auto-pnl-loss{color:#ff5567;font-weight:800}
.manual-trade .mt-auto-pnl-pending{color:#9daeb0;font-weight:500}
.manual-trade #manual-payout,.manual-trade #manual-confirm-payout{color:#29d599}
.manual-trade .mt-auto-record-ticker{font:10px ui-monospace,Consolas,monospace;color:#849a9c;overflow-wrap:anywhere}
.manual-trade .mt-auto-empty{color:#9faeb0;font-size:12px;line-height:1.6;padding:8px 0}
.manual-trade .mt-inputbox{height:55px;border:1px solid #32353a;border-radius:10px;display:flex;
  align-items:center;gap:8px;padding:0 18px;margin:18px 0 11px}
.manual-trade .mt-inputbox:focus-within{border-color:#e3c45c}
.manual-trade .mt-inputbox input{flex:1;min-width:0;font-size:17px;font-family:inherit;background:none;
  color:#f4f4f4;border:0;outline:0}
.manual-trade .mt-inputbox .mt-inputunit{color:#9d9fa4;font-size:17px;font-weight:750}
.manual-trade .mt-held-side{color:#20d5a1}
.manual-trade .mt-sell-holding{justify-content:space-between;font-size:14px;color:#a4a3aa}
.manual-trade .mt-sell-holding strong{font-size:19px;color:#f2f2f3;letter-spacing:.3px}
.manual-trade .mt-profit{font-size:11px;text-align:right;margin-top:3px}
.manual-trade .mt-profit.loss{color:#ff5567}
.manual-trade .mt-profit.gain{color:#29d599}
.manual-trade .mt-sub{color:#a4a3aa;font-size:13px;line-height:1.6;min-height:19px;margin-bottom:24px}
.manual-trade .mt-liquidity{display:none;margin:-10px 0 18px;color:#ff9c43;font-size:13px;font-weight:750}
.manual-trade .mt-liquidity.show{display:block}
.manual-trade .mt-rows{display:grid;gap:14px;margin:0 0 24px}
.manual-trade .mt-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;color:#a4a5ad;font-size:14px}
.manual-trade .mt-row .mt-number{color:#f4f4f4;font-weight:550;white-space:nowrap;text-align:right}
.manual-trade .mt-row .mt-big{font-size:27px;letter-spacing:-.6px}
.manual-trade .mt-row small{display:block;color:#29d599;font-size:11px;text-align:right}
.manual-trade .mt-action{width:100%;min-height:52px;border-radius:28px;background:#15d79a;border:2px solid #d9b949;
  outline:2px solid #564c26;outline-offset:2px;color:#001610;font-size:15px;font-weight:850;cursor:pointer}
.manual-trade .mt-action.sell{background:#ececec;color:#181818}
.manual-trade .mt-action:disabled{opacity:.46;cursor:not-allowed}
.manual-trade .mt-warning{font-size:12px;color:#dca9a9;margin-top:14px;line-height:1.6;min-height:19px;overflow-wrap:anywhere}
.manual-trade .mt-fine{font-size:11px;color:#81848d;margin-top:13px;line-height:1.6}
.manual-trade .mt-confirm{display:none;min-height:555px;background:#101116}
.manual-trade .mt-confirm.show{display:block}
.manual-trade .mt-confirm-top{padding:23px 24px;font-weight:800;letter-spacing:2px;color:#0cdaa0;
  border-bottom:1px solid #292b31;font-size:12px}
.manual-trade .mt-confirm-main{padding:24px 30px}
.manual-trade .mt-confirm-title{display:flex;gap:12px;align-items:center;font-size:20px;font-weight:800;line-height:1.5;margin-bottom:46px}
.manual-trade .mt-confirm-title strong{color:#ff4558}
.manual-trade .mt-confirm .mt-action{margin-top:30px;background:#17d99c;border:0;outline:0}
.manual-trade .mt-confirm-note{color:#a9aab0;font-size:12px;margin-top:14px}
.manual-trade .mt-hidden{display:none!important}
@media(max-width:650px){.context>.manual-trade{min-height:530px}.manual-trade .mt-top{padding:0 12px}.manual-trade .mt-main{padding:21px 17px}
 .manual-trade .mt-tabs{gap:11px}.manual-trade .mt-tab{font-size:12px;letter-spacing:1px}
 .manual-trade .mt-account-summary{gap:12px}.manual-trade .mt-account-value{font-size:11px}
 .manual-trade .mt-account-label{font-size:10px}.manual-trade .mt-units{font-size:10px;letter-spacing:1px;padding:6px 0}}
"""

SECTION = r"""
<article class="manual-trade" aria-label="Manual real-money Kalshi Buy and Sell" id="manual-trade">
 <div id="manual-entry">
  <div class="mt-top">
   <div class="mt-tabs">
    <button type="button" id="manual-buy" class="mt-tab active">BUY</button>
    <button type="button" id="manual-sell" class="mt-tab">SELL</button>
   </div>
   <div class="mt-account-summary" role="group" aria-label="Predictions account balances" title="Portfolio includes cash plus marked positions; it is not a guaranteed cash-out value.">
    <div class="mt-account-item">
     <strong class="mt-account-value" id="manual-cash">—</strong>
     <span class="mt-account-label">Cash</span>
    </div>
    <div class="mt-account-item portfolio">
     <strong class="mt-account-value" id="manual-portfolio">—</strong>
     <span class="mt-account-label">Portfolio</span>
    </div>
   </div>
   <select id="manual-units" class="mt-units" aria-label="Dollars or shares">
    <option value="DOLLARS">DOLLARS</option><option value="SHARES">SHARES</option>
   </select>
  </div>
  <div class="mt-main">
   <div class="mt-title"><span class="mt-btc" aria-hidden="true">₿</span>
    <span><span id="manual-held-side" class="mt-held-side mt-hidden"></span>BTC Up or Down - 15 minutes</span></div>
   <div class="mt-pills" id="manual-pills" role="group" aria-label="Choose market outcome">
    <button type="button" id="manual-up" class="mt-pill mt-up">UP —</button>
    <button type="button" id="manual-down" class="mt-pill mt-down active">DOWN —</button>
    <button type="button" id="manual-auto" class="mt-pill mt-auto" aria-pressed="false">AUTO</button>
   </div>
   <div class="mt-inputbox" id="manual-buy-inputbox">
    <input id="manual-amount" type="number" min="0.01" step="0.01" inputmode="decimal" placeholder="Enter amount" aria-label="Trade amount">
    <span class="mt-inputunit" id="manual-inputunit">$</span>
   </div>
   <div class="mt-inputbox mt-sell-holding mt-hidden" id="manual-sell-holding" aria-label="Shares held for full-position sale">
    <span>Shares</span><strong id="manual-sell-shares">—</strong>
   </div>
   <div class="mt-sub" id="manual-account">Connecting to Kalshi account…</div>
   <div class="mt-liquidity" id="manual-liquidity" role="status">ⓘ No liquidity</div>
   <div class="mt-rows">
    <div class="mt-row"><span id="manual-quote-label">Odds</span><span class="mt-number" id="manual-odds">—</span></div>
    <div class="mt-row"><span id="manual-cost-label">Estimated cost incl. fee</span>
     <span class="mt-number"><span id="manual-estimated">—</span><span class="mt-profit mt-hidden" id="manual-estimated-pl"></span></span></div>
    <div class="mt-row" id="manual-payout-row"><span id="manual-payout-label">Max payout</span><span class="mt-number mt-big" id="manual-payout">—</span></div>
   </div>
   <button type="button" id="manual-submit" class="mt-action" disabled>Buy with 1-Click</button>
   <button type="button" id="manual-auto-arm" class="mt-auto-arm mt-hidden" disabled>Arm AutoTrade</button>
   <div class="mt-auto-status mt-hidden" id="manual-auto-status" role="status" aria-live="polite"></div>
   <div class="mt-warning" id="manual-status" role="status" aria-live="polite">Awaiting a live Kalshi market…</div>
   <div class="mt-fine">Real orders · quotes and fees can change · unfilled IOC quantity is canceled</div>
   <section class="mt-auto-history mt-hidden" id="manual-auto-history" aria-label="AutoTrade execution history">
    <div class="mt-auto-history-head"><span>AutoTrade History</span><span class="mt-auto-history-count" id="manual-auto-history-count"></span></div>
    <div class="mt-auto-history-list" id="manual-auto-history-list" aria-live="polite"><div class="mt-auto-empty">No AutoTrades recorded yet.</div></div>
   </section>
  </div>
 </div>
 <div id="manual-confirm" class="mt-confirm" aria-live="polite">
  <div class="mt-confirm-top" id="manual-confirm-heading">ORDER COMPLETED</div>
  <div class="mt-confirm-main">
   <div class="mt-confirm-title"><span class="mt-btc" aria-hidden="true">₿</span>
    <span id="manual-confirm-title">BTC Up or Down - 15 minutes</span></div>
   <div class="mt-rows">
    <div class="mt-row"><span>Shares filled</span><span class="mt-number" id="manual-confirm-count">—</span></div>
    <div class="mt-row"><span>Odds</span><span class="mt-number" id="manual-confirm-price">—</span></div>
    <div class="mt-row"><span id="manual-confirm-cost-label">Cost</span><span class="mt-number" id="manual-confirm-cost">—</span></div>
    <div class="mt-row"><span>Fees</span><span class="mt-number" id="manual-confirm-fee">—</span></div>
    <div class="mt-row"><span id="manual-confirm-payout-label">Max payout</span><span class="mt-number mt-big" id="manual-confirm-payout">—</span></div>
    <div class="mt-row"><span>Exchange order</span><span class="mt-number" id="manual-confirm-order">—</span></div>
   </div>
   <div class="mt-confirm-note" id="manual-confirm-note">Figures shown are exchange-reported, not simulated.</div>
   <button type="button" class="mt-action" id="manual-done">Done</button>
  </div>
 </div>
</article>
"""

SCRIPT = r"""
<script>
(function(){
 const byId=id=>document.getElementById(id);
 const shown=(id,value)=>{byId(id).textContent=value};
 const money=v=>v!=null&&Number.isFinite(Number(v))?"$"+Number(v).toFixed(2):"—";
 // Account display values arrive already formatted from Kalshi's integer cents.
 // Do not round spending balances up in the Cash/Portfolio header.
 const exactMoney=v=>typeof v==="string"&&/^\d+\.\d{2}$/.test(v)?"$"+v:"—";
 const cents=v=>v!=null&&Number.isFinite(Number(v))?(Number(v)*100).toLocaleString("en-US",{maximumFractionDigits:2})+"¢":"—";
 const priceCents=cents;
 const probabilityPercent=v=>v!=null&&Number.isFinite(Number(v))?(Number(v)*100).toLocaleString("en-US",{maximumFractionDigits:2})+"% chance":"—";
 let action="BUY",outcome="DOWN",snapshot=null,quote=null,placing=false,previewGeneration=0;
 let buyUnits="DOLLARS",marketGeneration=0,autoSelected=false,autoBusy=false;
 let autoCurrent={state:"IDLE"},lastHistoryFetch=0,autoHistoryRows=[];
 const signedPnl=v=>typeof v==="string"&&/^-?\d+(\.\d+)?$/.test(v)?
    (v.startsWith("-")?"−":"+")+"$"+v.replace(/^-/, ""):"—";
 function renderAutoPayout(){
  if(action!=="BUY"||!autoSelected)return;
  const current=autoHistoryRows.find(entry=>snapshot&&entry.ticker===snapshot.ticker&&entry.max_payout_dollars!=null);
  const latest=current||autoHistoryRows.find(entry=>entry.max_payout_dollars!=null);
  shown("manual-payout-label",current?"Max payout (this trade)":latest?"Last AutoTrade max payout":"Max payout");
  shown("manual-payout",latest?"+"+money(latest.max_payout_dollars):"—");
 }
 const setLiquidity=visible=>byId("manual-liquidity").classList.toggle("show",visible);
 function renderAccountSummary(value){
  shown("manual-cash",value?exactMoney(value.cash_display):"—");
  shown("manual-portfolio",value?exactMoney(value.portfolio_display):"—");
 }
 function clearQuote(){
  quote=null;byId("manual-submit").disabled=true;
  shown("manual-odds","—");shown("manual-estimated","—");shown("manual-payout","—");
  byId("manual-estimated-pl").classList.add("mt-hidden");
 }
 function heldPosition(){
  const n=Number(snapshot?.position_yes);
  return snapshot&&Number.isFinite(n)?n:0;
 }
 function renderHolding(){
  const held=heldPosition();
  if(action==="SELL"){
   outcome=held>0?"UP":held<0?"DOWN":"";
   shown("manual-sell-shares",snapshot?Math.abs(held).toFixed(2):"—");
   shown("manual-held-side",held>0?"Up · ":held<0?"Down · ":"");
   shown("manual-account",snapshot?(held!==0?"Your position: "+Math.abs(held).toFixed(2)+" "+(held>0?"Yes":"No"):"No position held in this round"):"Loading your position…");
  }else{
   shown("manual-account",snapshot?"Predictions account · "+exactMoney(snapshot.cash_display)+" available":"Connecting to Kalshi account…");
  }
 }
 const autoPending=()=>["WAITING","EXECUTING"].includes(autoCurrent.state);
 function renderAuto(){
  const selected=action==="BUY"&&autoSelected;
  const armed=autoPending();
  byId("manual-auto").classList.toggle("active",selected);
  byId("manual-auto").setAttribute("aria-pressed",selected?"true":"false");
  byId("manual-up").classList.toggle("active",!autoSelected&&outcome==="UP");
  byId("manual-down").classList.toggle("active",!autoSelected&&outcome==="DOWN");
  byId("manual-auto-arm").classList.toggle("mt-hidden",!selected);
  byId("manual-auto-status").classList.toggle("mt-hidden",!selected);
  byId("manual-auto-history").classList.toggle("mt-hidden",!selected);
  byId("manual-auto-arm").classList.toggle("armed",armed);
  byId("manual-units").value=selected?"DOLLARS":action==="SELL"?"SHARES":buyUnits;
  byId("manual-units").disabled=selected||action==="SELL"||armed;
  byId("manual-amount").disabled=armed;
  const amount=Number(byId("manual-amount").value);
  const canArm=Number.isFinite(amount)&&amount>=0.01&&amount<=100000;
  const button=byId("manual-auto-arm");
  button.textContent=armed?(autoCurrent.state==="EXECUTING"?"Executing locked forecast…":"Cancel AutoTrade"):"Arm AutoTrade";
  button.disabled=autoBusy||autoCurrent.state==="EXECUTING"||(!armed&&!canArm);
  const messages={
   WAITING:"ARMED · "+autoCurrent.amount+" USD each round · "+(autoCurrent.message||"Monitoring 90% live / final forecast")+" ("+(autoCurrent.ticker||"current round")+")",
   EXECUTING:"Executing AutoTrade: "+(autoCurrent.outcome||"—")+" · "+(autoCurrent.message||"checking Kalshi"),
   FILLED:"Order filled · "+(autoCurrent.outcome||"")+" · "+(autoCurrent.order_id||""),
   PARTIAL:"Partially filled · check Kalshi orders for remaining position",
   NO_FILL:"No shares filled · no automatic retry",
   SKIPPED:autoCurrent.message||"Final forecast SKIP or confidence under 69% · no trade",
   EXPIRED:"Market changed or final forecast too late · no trade",
   CANCELLED:"AutoTrade cancelled. Nothing submitted.",
   ERROR:"AutoTrade stopped before order · check live market and quote",
   UNKNOWN:"Order outcome unknown · check Kalshi orders; do not retry",
   REVIEW:"Reversal not confirmed · manual review required before further AUTO orders"
  };
  shown("manual-auto-status",messages[autoCurrent.state]||"Live ≥90% at ≤8m, otherwise final lock. Opposite final after early fill sells then buys the final side. Arm AutoTrade to begin.");
  if(selected&&armed)shown("manual-status",autoCurrent.message||"Waiting for locked final forecast");
  if(selected)renderAutoPayout();
 }
 function setMode(next){
  if(placing)return;
  if(autoPending()){shown("manual-status","Cancel AutoTrade before switching modes.");return}
  if(action==="BUY"&&!autoSelected)buyUnits=byId("manual-units").value;
  action=next;
  autoSelected=false;
  byId("manual-buy").classList.toggle("active",next==="BUY");
  byId("manual-sell").classList.toggle("active",next==="SELL");
  byId("manual-submit").classList.toggle("sell",next==="SELL");
  byId("manual-pills").classList.toggle("mt-hidden",next==="SELL");
  byId("manual-buy-inputbox").classList.toggle("mt-hidden",next==="SELL");
  byId("manual-sell-holding").classList.toggle("mt-hidden",next==="BUY");
  byId("manual-held-side").classList.toggle("mt-hidden",next==="BUY");
  byId("manual-payout-row").classList.toggle("mt-hidden",next==="SELL");
  byId("manual-units").value=next==="SELL"?"SHARES":buyUnits;
  byId("manual-units").disabled=next==="SELL";
  shown("manual-quote-label",next==="BUY"?"Odds":"Average price");
  shown("manual-cost-label",next==="BUY"?"Estimated cost incl. fee":"Cash out (estimated)");
  shown("manual-submit",next==="BUY"?"Buy with 1-Click":"Sell with 1-Click");
  shown("manual-payout-label","Max payout");
  renderAuto();renderHolding();refreshPreview();
 }
 function setSide(next){
  if(placing||action==="SELL"||autoPending())return;
  autoSelected=false;
  outcome=next;
  renderAuto();
  refreshPreview();
 }
 function selectAuto(){
  if(placing||action==="SELL")return;
  if(!autoSelected)buyUnits=byId("manual-units").value;
  autoSelected=true;
  renderAuto();
  refreshAutoHistory(true);
  refreshPreview();
 }
 const url=path=>"/api/manual-trade/"+path;
 const autoUrl=path=>"/api/armed-trade/"+path;
 async function autoJSON(path,options){
  const response=await fetch(autoUrl(path),{cache:"no-store",...options});
  const body=await response.json();
  if(!response.ok)throw Error(body.error||"HTTP "+response.status);
  return body;
 }

 async function refreshAutoHistory(force=false){
  if(!autoSelected||action!=="BUY")return;
  if(!force&&Date.now()-lastHistoryFetch<8000)return;
  lastHistoryFetch=Date.now();
  const list=byId("manual-auto-history-list");
  try{
   const result=await autoJSON("history");
   if(!autoSelected||action!=="BUY")return;
   if(!Array.isArray(result.entries))throw Error("Invalid history response");
   autoHistoryRows=result.entries;
   renderAutoPayout();
   list.replaceChildren();
   shown("manual-auto-history-count",result.entries.length?result.entries.length+" recent markets":"");
   if(!result.entries.length){
    const empty=document.createElement("div");empty.className="mt-auto-empty";
    empty.textContent="No AutoTrades recorded yet.";list.appendChild(empty);return;
   }
   for(const entry of result.entries){
    const item=document.createElement("div");item.className="mt-auto-record";
    const header=document.createElement("div");header.className="mt-auto-record-top";
    const side=document.createElement("span");
    side.className="mt-auto-record-side "+(entry.outcome==="UP"?"up":"down");
    side.textContent=entry.outcome==="UP"?"UP":"DOWN";
    const state=document.createElement("span");
    state.className="mt-auto-record-status "+(entry.status==="FILLED"?"filled":"warn");
    state.textContent=entry.status==="NO_FILL"?"NO FILL":String(entry.status||"UNKNOWN");
    header.append(side,state);item.appendChild(header);
    const meta=document.createElement("div");meta.className="mt-auto-record-sub";
    const amount=document.createElement("span");amount.textContent="Amount "+money(entry.amount);
    const time=document.createElement("span");
    const ms=Number(entry.attempted_at_ns)/1000000;
    time.textContent=Number.isFinite(ms)?new Date(ms).toLocaleString():"Time unavailable";
    meta.append(amount,time);item.appendChild(meta);
    const detail=document.createElement("div");detail.className="mt-auto-record-sub";
    const confidence=document.createElement("span");
    confidence.textContent=entry.confidence!=null?"Confidence "+(100*Number(entry.confidence)).toFixed(1)+"%":"";
    const fill=document.createElement("span");
    fill.textContent=entry.fill_count!=null?"Filled "+entry.fill_count+" shares":entry.status==="UNKNOWN"?"Verify Kalshi order":"";
    detail.append(confidence,fill);item.appendChild(detail);
    const finance=document.createElement("div");finance.className="mt-auto-pnl-row";
    const pnl=document.createElement("strong");
    if(entry.realized_pnl_dollars!=null){
     const losing=String(entry.realized_pnl_dollars).startsWith("-");
     pnl.className=losing?"mt-auto-pnl-loss":"mt-auto-pnl-gain";
     if(losing){
      pnl.textContent="Loss "+signedPnl(entry.realized_pnl_dollars);
     }else{
      pnl.textContent="Profit "+signedPnl(entry.realized_pnl_dollars);
     }
    }else{
     pnl.className="mt-auto-pnl-pending";
     pnl.textContent=entry.pnl_status==="PENDING"?"P/L pending settlement":
       entry.pnl_status==="MIXED"?"P/L unavailable · other market trades":
       entry.status==="NO_FILL"?"No shares filled":"P/L unavailable · verify Kalshi";
    }
    finance.append(pnl);item.appendChild(finance);
    if(Array.isArray(entry.stages)&&entry.stages.length){
      const details=document.createElement("div");details.className="mt-auto-record-sub";
      const words=entry.stages.map(stage=>{
        const name=String(stage.stage||"").replaceAll("_"," ");
        const dir=stage.outcome==="UP"?"UP":"DOWN";
        const outcome=String(stage.status||"UNKNOWN").replaceAll("_"," ");
        return name+" "+dir+": "+outcome+(stage.fill_count!=null?" ("+stage.fill_count+" shares)":"");
      });
      details.textContent=words.join(" · ");
      item.appendChild(details);
    }
    const ticker=document.createElement("div");ticker.className="mt-auto-record-ticker";
    ticker.textContent=String(entry.ticker||"");
    item.appendChild(ticker);list.appendChild(item);
   }
  }catch(e){
   if(!autoSelected)return;
   autoHistoryRows=[];renderAutoPayout();
   list.replaceChildren();
   const el=document.createElement("div");el.className="mt-auto-empty";
   el.textContent="History unavailable: "+e.message;list.appendChild(el);
  }
 }
 async function refreshAuto(){
  try{
   autoCurrent=await autoJSON("status");
   if(autoPending()&&action==="BUY"&&!autoSelected){autoSelected=true;renderAuto();refreshPreview();}
   renderAuto();
   if(autoSelected)refreshAutoHistory();
  }catch(e){if(autoSelected)shown("manual-auto-status","AutoTrade status unavailable: "+e.message)}
 }
 async function armOrCancel(){
  if(autoBusy||action!=="BUY"||!autoSelected)return;
  if(autoCurrent.state==="EXECUTING")return;
  autoBusy=true;renderAuto();
  try{
   const pending=autoCurrent.state==="WAITING";
   const path=pending?"cancel":"arm";
   const value=pending?{}:{amount:byId("manual-amount").value};
   autoCurrent=await autoJSON(path,{
    method:"POST",
    headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"armed-trade"},
    body:JSON.stringify(value)
   });
   renderAuto();refreshPreview();
  }catch(e){shown("manual-auto-status","AutoTrade action rejected: "+e.message)}
  finally{autoBusy=false;renderAuto()}
 }
 async function fetchJSON(path,options){
  const response=await fetch(url(path),{cache:"no-store",...options});
  const body=await response.json();
  if(!response.ok)throw Error(body.error||"HTTP "+response.status);
  return body;
 }
 async function refreshMarket(){
  if(placing)return;
  const generation=++marketGeneration;
  try{
   const value=await fetchJSON("market");
   if(generation!==marketGeneration||placing)return;
   snapshot=value;
   renderAccountSummary(value);
   renderHolding();
   shown("manual-up",value.up_ask==null?"UP —":"UP "+priceCents(value.up_ask));
   shown("manual-down",value.down_ask==null?"DOWN —":"DOWN "+priceCents(value.down_ask));
   await refreshPreview();
  }catch(e){
   if(generation!==marketGeneration||placing)return;
   snapshot=null;previewGeneration++;clearQuote();setLiquidity(false);
   renderAccountSummary(null);
   shown("manual-status",e.message);
   shown("manual-account","Kalshi market or credentials unavailable");
  }
 }
 function renderProfit(value){
  const cost=Number(snapshot?.position_exposure_dollars);
  const cash=Number(value.amount_estimate);
  const el=byId("manual-estimated-pl");
  if(snapshot?.position_exposure_dollars==null||!Number.isFinite(cost)||!Number.isFinite(cash)||cost<0){
   el.classList.add("mt-hidden");return;
  }
  const net=cash-cost;
  el.classList.remove("mt-hidden","loss","gain");
  el.classList.add(net<0?"loss":"gain");
  el.textContent=(net<0?"−":"+")+money(Math.abs(net))+" est. P/L";
 }
 async function refreshPreview(){
  if(placing)return;
  const generation=++previewGeneration;
  clearQuote();setLiquidity(false);
  const amount=byId("manual-amount").value,units=byId("manual-units").value;
  shown("manual-inputunit",units==="DOLLARS"?"$":"SHARES");
  if(!snapshot){shown("manual-status","Awaiting live market");return}
  if(action==="BUY"&&autoSelected){
   shown("manual-odds","At final lock");
   renderAutoPayout();
   shown("manual-status",autoPending()?(autoCurrent.message||"Waiting for 90% live signal or locked final forecast"):"Enter dollars to Arm AutoTrade. It can enter early at ≥90% confidence and reverse on an opposite final lock.");
   return;
  }
  if(action==="SELL"){
   const held=heldPosition();
   if(held===0){shown("manual-status","No position to sell in this round");return}
   shown("manual-odds",priceCents(snapshot.average_entry_price));
   try{
    const value=await fetchJSON("sell-preview");
    if(generation!==previewGeneration||!snapshot||value.ticker!==snapshot.ticker||action!=="SELL")return;
    // The server, not browser input, decides which shares are owned and sold.
    if(value.action!=="SELL"||value.sell_all!==true||value.outcome!==(held>0?"UP":"DOWN")
       ||Number(value.count)!==Math.abs(held))return;
    quote=value;
    shown("manual-estimated",money(value.amount_estimate));
    renderProfit(value);
    byId("manual-submit").disabled=false;
    shown("manual-status","Sell all "+Number(value.count).toFixed(2)+" shares · estimate includes fees; IOC may partially fill");
   }catch(e){
    if(generation!==previewGeneration)return;
    const missing=e.message.includes("liquidity")||e.message.includes("No liquidity");
    setLiquidity(missing);
    shown("manual-status",missing?"Not enough liquidity to sell entire position":e.message);
   }
   return;
  }
  const ask=outcome==="UP"?snapshot.up_ask:snapshot.down_ask;
  if(ask==null){setLiquidity(true);shown("manual-status","Selected side has no liquidity");return}
  if(!(Number(amount)>0)){shown("manual-status","Enter a trade amount to receive a live quote.");return}
  try{
   const qs=new URLSearchParams({action,outcome,units,amount});
   const value=await fetchJSON("preview?"+qs.toString());
   if(generation!==previewGeneration||!snapshot||value.ticker!==snapshot.ticker
     ||action!=="BUY"||value.action!==action||value.outcome!==outcome
     ||value.units!==units||byId("manual-amount").value!==amount)return;
   quote=value;
   shown("manual-odds",probabilityPercent(value.unit_price));
   shown("manual-estimated",money(value.amount_estimate)+" est.");
   shown("manual-payout","+"+money(value.maximum_payout));
   byId("manual-submit").disabled=false;
   shown("manual-status","Live quote · "+Number(value.count).toFixed(2)+" shares · est. fee "+money(value.fee_estimate));
  }catch(e){
   if(generation!==previewGeneration)return;
   clearQuote();
   const missing=e.message.includes("No liquidity");
   setLiquidity(missing);
   shown("manual-status",missing?"Selected side has no liquidity":e.message);
  }
 }
 async function place(){
  if(placing||!quote||byId("manual-submit").disabled)return;
  placing=true;const button=byId("manual-submit");button.disabled=true;
  shown("manual-status","Sending real Kalshi order…");
  const current=quote;quote=null;
  try{
   const result=await fetchJSON("order",{
    method:"POST",headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"manual-trade"},
    body:JSON.stringify({preview_id:current.preview_id})
   });
   shown("manual-confirm-heading",result.status==="NO_FILL"?"ORDER NOT FILLED":
     result.status==="PARTIAL"?"ORDER PARTIALLY FILLED":"ORDER COMPLETED");
   shown("manual-confirm-title",(result.action==="BUY"?"Buy ":"Sell ")+result.outcome+" · BTC Up or Down - 15 minutes");
   shown("manual-confirm-count",result.fill_count);
   shown("manual-confirm-price",result.actual_unit_price?probabilityPercent(result.actual_unit_price):"Not filled");
   shown("manual-confirm-cost-label",result.action==="BUY"?"Cost":"Shares sold");
   shown("manual-confirm-cost",result.action==="BUY"?money(result.actual_cost):result.fill_count);
   shown("manual-confirm-fee",result.actual_fees===null?"Check exchange fills":money(result.actual_fees));
   shown("manual-confirm-payout-label",result.action==="BUY"?"Max payout":"Cash out");
   shown("manual-confirm-payout",result.action==="BUY"&&result.max_payout!=null?"+"+money(result.max_payout):money(result.actual_proceeds));
   shown("manual-confirm-order",result.order_id.slice(0,14)+"…");
   shown("manual-confirm-note","Exchange-reported result only. Check Kalshi for final balance and fills.");
   byId("manual-entry").classList.add("mt-hidden");
   byId("manual-confirm").classList.add("show");
  }catch(e){
   shown("manual-status","Order not confirmed: "+e.message+" · Check Kalshi before retrying.");
  }finally{placing=false}
 }
 byId("manual-buy").addEventListener("click",()=>setMode("BUY"));
 byId("manual-sell").addEventListener("click",()=>setMode("SELL"));
 byId("manual-up").addEventListener("click",()=>setSide("UP"));
 byId("manual-down").addEventListener("click",()=>setSide("DOWN"));
 byId("manual-auto").addEventListener("click",selectAuto);
 byId("manual-auto-arm").addEventListener("click",armOrCancel);
 byId("manual-units").addEventListener("change",()=>{
  if(action==="BUY")buyUnits=byId("manual-units").value;
  refreshPreview();
 });
 let amountTimer;
 byId("manual-amount").addEventListener("input",()=>{
  clearTimeout(amountTimer);amountTimer=setTimeout(refreshPreview,400);
 });
 byId("manual-submit").addEventListener("click",place);
 byId("manual-done").addEventListener("click",()=>{
  byId("manual-entry").classList.remove("mt-hidden");
  byId("manual-confirm").classList.remove("show");
  byId("manual-amount").value="";
  refreshMarket();
 });
 refreshMarket();refreshAuto();
 setInterval(refreshMarket,4500);
 setInterval(refreshAuto,1800);
})();
</script>
"""


def integrate_manual_trade(page: str) -> str:
    if '<div class="context">' not in page or 'id="market-ticker"' not in page:
        raise ValueError("Original dashboard context not found; will not alter layout")
    page = page.replace("</style>", STYLE + "\n</style>", 1)
    page = page.replace('<span class="chip">READ-ONLY</span>', '<span class="chip">MANUAL TRADING</span>', 1)
    page = page.replace('No automatic trades.</p>', 'User-initiated real orders; no automatic trades.</p>', 1)
    page = page.replace('<div class="context">', '<div class="context">\n' + SECTION, 1)
    page = page.replace("</body>", SCRIPT + "\n</body>", 1)
    return page
