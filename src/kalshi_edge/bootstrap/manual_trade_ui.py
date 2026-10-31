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
.manual-trade .mt-top{height:68px;display:flex;justify-content:space-between;align-items:center;
  border-bottom:1px solid #26282e;padding:0 20px}
.manual-trade .mt-tabs{display:flex;gap:18px}
.manual-trade .mt-tab{font-size:14px;font-weight:850;letter-spacing:2px;color:#67666c;background:none;
  border:0;cursor:pointer;padding:23px 0}
.manual-trade .mt-tab.active{color:#f4f4f4}
.manual-trade .mt-units{background:transparent;color:#f1f1f4;border:0;font-weight:850;font-size:12px;
  letter-spacing:2px;cursor:pointer;padding:10px}
.manual-trade .mt-main{padding:22px 20px 25px}
.manual-trade .mt-title{display:flex;gap:12px;align-items:center;font-size:clamp(15px,1.5vw,23px);
  font-weight:850;letter-spacing:-.5px;line-height:1.3;margin-bottom:22px}
.manual-trade .mt-btc{display:grid;place-items:center;flex:none;width:36px;height:36px;border-radius:8px;
  background:#f89a3d;color:#fff;font-size:23px;font-weight:800}
.manual-trade .mt-pills{display:flex;width:min(100%,286px);border-radius:24px;overflow:hidden;
  background:#26272b;border:1px solid #35363b;margin-bottom:20px}
.manual-trade .mt-pill{flex:1;min-width:0;border:0;background:transparent;color:#f0f0f0;padding:10px 7px;
  border-radius:24px;font-size:14px;font-weight:850;letter-spacing:1px;cursor:pointer}
.manual-trade .mt-pill.mt-up.active{background:#25bf85;color:#071e16}
.manual-trade .mt-pill.mt-down.active{background:#ff4458;color:#09090b}
.manual-trade .mt-inputbox{height:55px;border:1px solid #32353a;border-radius:10px;display:flex;
  align-items:center;gap:8px;padding:0 18px;margin:18px 0 11px}
.manual-trade .mt-inputbox:focus-within{border-color:#e3c45c}
.manual-trade .mt-inputbox input{flex:1;min-width:0;font-size:17px;font-family:inherit;background:none;
  color:#f4f4f4;border:0;outline:0}
.manual-trade .mt-inputbox .mt-inputunit{color:#9d9fa4;font-size:17px;font-weight:750}
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
@media(max-width:650px){.context>.manual-trade{min-height:530px}.manual-trade .mt-top{padding:0 16px}.manual-trade .mt-main{padding:21px 17px}}
"""

SECTION = r"""
<article class="manual-trade" aria-label="Manual real-money Kalshi Buy and Sell" id="manual-trade">
 <div id="manual-entry">
  <div class="mt-top">
   <div class="mt-tabs">
    <button type="button" id="manual-buy" class="mt-tab active">BUY</button>
    <button type="button" id="manual-sell" class="mt-tab">SELL</button>
   </div>
   <select id="manual-units" class="mt-units" aria-label="Dollars or shares">
    <option value="DOLLARS">DOLLARS</option><option value="SHARES">SHARES</option>
   </select>
  </div>
  <div class="mt-main">
   <div class="mt-title"><span class="mt-btc" aria-hidden="true">₿</span>
    <span>BTC Up or Down - 15 minutes</span></div>
   <div class="mt-pills" role="group" aria-label="Choose market outcome">
    <button type="button" id="manual-up" class="mt-pill mt-up">UP —</button>
    <button type="button" id="manual-down" class="mt-pill mt-down active">DOWN —</button>
   </div>
   <div class="mt-inputbox">
    <input id="manual-amount" type="number" min="0.01" step="0.01" inputmode="decimal" placeholder="Enter amount" aria-label="Trade amount">
    <span class="mt-inputunit" id="manual-inputunit">$</span>
   </div>
   <div class="mt-sub" id="manual-account">Connecting to Kalshi account…</div>
   <div class="mt-liquidity" id="manual-liquidity" role="status">ⓘ No liquidity</div>
   <div class="mt-rows">
    <div class="mt-row"><span id="manual-quote-label">Odds</span><span class="mt-number" id="manual-odds">—</span></div>
    <div class="mt-row"><span id="manual-cost-label">Estimated cost incl. fee</span><span class="mt-number" id="manual-estimated">—</span></div>
    <div class="mt-row"><span id="manual-payout-label">Max payout</span><span class="mt-number mt-big" id="manual-payout">—</span></div>
   </div>
   <button type="button" id="manual-submit" class="mt-action" disabled>Buy with 1-Click</button>
   <div class="mt-warning" id="manual-status" role="status" aria-live="polite">Awaiting a live Kalshi market…</div>
   <div class="mt-fine">Real orders · quotes and fees can change · unfilled IOC quantity is canceled</div>
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
 const cents=v=>v!=null&&Number.isFinite(Number(v))?(Number(v)*100).toLocaleString("en-US",{maximumFractionDigits:2})+"¢":"—";
 const priceCents=cents;
 const probabilityPercent=v=>v!=null&&Number.isFinite(Number(v))?(Number(v)*100).toLocaleString("en-US",{maximumFractionDigits:2})+"% chance":"—";
 let action="BUY",outcome="DOWN",snapshot=null,quote=null,placing=false,previewGeneration=0;
 const setLiquidity=visible=>byId("manual-liquidity").classList.toggle("show",visible);
 function clearQuote(){
  quote=null;byId("manual-submit").disabled=true;
  shown("manual-odds","—");shown("manual-estimated","—");shown("manual-payout","—");
 }
 function setMode(next){action=next;byId("manual-buy").classList.toggle("active",next==="BUY");
  byId("manual-sell").classList.toggle("active",next==="SELL");
  byId("manual-submit").classList.toggle("sell",next==="SELL");
  shown("manual-quote-label",next==="BUY"?"Odds":"Current exit price");
  shown("manual-cost-label",next==="BUY"?"Estimated cost incl. fee":"Estimated cash out");
  shown("manual-payout-label",next==="BUY"?"Max payout":"Shares to sell");
  shown("manual-submit",next==="BUY"?"Buy with 1-Click":"Sell with 1-Click");
  refreshPreview();
 }
 function setSide(next){outcome=next;byId("manual-up").classList.toggle("active",next==="UP");
  byId("manual-down").classList.toggle("active",next==="DOWN");refreshPreview();
 }
 const url=path=>"/api/manual-trade/"+path;
 async function fetchJSON(path,options){
  const response=await fetch(url(path),{cache:"no-store",...options});
  const body=await response.json();
  if(!response.ok)throw Error(body.error||"HTTP "+response.status);
  return body;
 }
 async function refreshMarket(){
  if(placing)return;
  try{
   const value=await fetchJSON("market");
   snapshot=value;shown("manual-account",action==="BUY"?"Predictions account · "+money(value.available_balance)+" available":
     "Your position: "+(Math.abs(Number(value.position_yes))||0).toFixed(2)+" "+(Number(value.position_yes)>=0?"Yes":"No"));
   shown("manual-up",value.up_ask==null?"UP —":"UP "+priceCents(value.up_ask));
   shown("manual-down",value.down_ask==null?"DOWN —":"DOWN "+priceCents(value.down_ask));
   await refreshPreview();
  }catch(e){snapshot=null;previewGeneration++;clearQuote();setLiquidity(false);shown("manual-status",e.message);
   shown("manual-account","Kalshi market or credentials unavailable");
  }
 }
 async function refreshPreview(){
  if(placing)return;
  const generation=++previewGeneration;
  clearQuote();setLiquidity(false);
  const amount=byId("manual-amount").value,units=byId("manual-units").value;
  shown("manual-inputunit",units==="DOLLARS"?"$":"SHARES");
  if(!snapshot){shown("manual-status","Awaiting live market");return}
  // Buying a side with an empty book is not a zero-percent quote.
  // SELL uses the opposite book and is independently checked by preview.
  const ask=outcome==="UP"?snapshot.up_ask:snapshot.down_ask;
  if(action==="BUY"&&ask==null){
   setLiquidity(true);
   shown("manual-status","Selected side has no liquidity");
   return;
  }
  if(!(Number(amount)>0)){shown("manual-status","Enter a trade amount to receive a live quote.");return}
  try{
   const qs=new URLSearchParams({action,outcome,units,amount});
   const value=await fetchJSON("preview?"+qs.toString());
   if(generation!==previewGeneration||!snapshot||value.ticker!==snapshot.ticker
     ||value.action!==action||value.outcome!==outcome
     ||value.units!==units||byId("manual-amount").value!==amount)return;
   quote=value;shown("manual-odds",probabilityPercent(value.unit_price));
   shown("manual-estimated",money(value.amount_estimate)+" est.");
   shown("manual-payout",action==="BUY"?money(value.maximum_payout):Number(value.count).toFixed(2));
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
  if(placing||!quote)return;
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
   shown("manual-confirm-payout",result.action==="BUY"?money(result.max_payout):money(result.actual_proceeds));
   shown("manual-confirm-order",result.order_id.slice(0,14)+"…");
   shown("manual-confirm-note","Exchange-reported result only. Check Kalshi for final balance and fills.");
   byId("manual-entry").classList.add("mt-hidden");
   byId("manual-confirm").classList.add("show");
  }catch(e){shown("manual-status","Order not confirmed: "+e.message+" · Check Kalshi before retrying.");}
  finally{placing=false}
 }
 byId("manual-buy").addEventListener("click",()=>setMode("BUY"));
 byId("manual-sell").addEventListener("click",()=>setMode("SELL"));
 byId("manual-up").addEventListener("click",()=>setSide("UP"));
 byId("manual-down").addEventListener("click",()=>setSide("DOWN"));
 byId("manual-units").addEventListener("change",refreshPreview);
 let amountTimer;byId("manual-amount").addEventListener("input",()=>{
  clearTimeout(amountTimer);amountTimer=setTimeout(refreshPreview,400);
 });
 byId("manual-submit").addEventListener("click",place);
 byId("manual-done").addEventListener("click",()=>{
  byId("manual-entry").classList.remove("mt-hidden");
  byId("manual-confirm").classList.remove("show");byId("manual-amount").value="";
  refreshMarket();
 });
 refreshMarket();setInterval(refreshMarket,4500);
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
