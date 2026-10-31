"""Screenshot-matched manual Kalshi trading card inside the existing context column.

Keep original Market Snapshot and Prediction Status nodes hidden, NOT removed:
the legacy live-dashboard renderer still writes to those IDs.
"""
from __future__ import annotations

STYLE = r"""
/* Manual trade card replaces only the two context-column display cards. */
.context.manual-trade-context{display:flex;flex-direction:column;gap:0;min-width:0}
.context.manual-trade-context>.context-header,
.context.manual-trade-context>.health{display:none!important}
.manual-trade-card{padding:0!important;background:#0d0e11!important;border:1px solid #282a30!important;border-radius:19px;overflow:hidden;box-shadow:0 12px 40px #0002;color:#f4f4f5}
.manual-trade-card *{box-sizing:border-box}
.mt-head{display:flex;align-items:center;justify-content:space-between;min-height:65px;padding:0 21px;border-bottom:1px solid #25272d}
.mt-tabs{display:flex;gap:20px}
.mt-tab{border:0;background:none;color:#7c7c85;font-size:13px;font-weight:900;letter-spacing:2.2px;padding:23px 0;cursor:pointer}
.mt-tab.active{color:#f4f4f6}
.mt-selector{background:none;color:#f5f5f6;border:0;font-family:inherit;font-weight:900;letter-spacing:1.9px;font-size:12px;max-width:130px;cursor:pointer}
.mt-selector option{background:#17181d;color:#fff}
.mt-inner{padding:20px 21px 24px}
.mt-market{display:flex;align-items:center;gap:13px;margin-bottom:20px}
.mt-btc{display:grid;place-items:center;width:38px;height:38px;background:#fb9228;color:#fff;border-radius:9px;font-weight:950;font-size:26px;flex-shrink:0}
.mt-market-title{font-weight:850;font-size:clamp(17px,2vw,23px);line-height:1.17;color:#e9e9ed;letter-spacing:-.6px}
.mt-market-ticker{font-size:10px;color:#83858e;margin-top:6px;word-break:break-word}
.mt-directions{display:flex;background:#25262b;border-radius:30px;overflow:hidden;margin:0 0 21px;max-width:320px}
.mt-side{flex:1;text-align:center;color:#ececef;font-weight:900;border:0;background:transparent;border-radius:30px;padding:10px 13px;letter-spacing:1px;white-space:nowrap;font-size:13px;cursor:pointer}
.mt-side.active.yes{background:#21cfa1;color:#07100f}
.mt-side.active.no{background:#ff4163;color:#060608}
.mt-side:disabled,.mt-tab:disabled{opacity:.5;cursor:not-allowed}
.mt-input-wrap{display:flex;align-items:center;justify-content:space-between;border:1px solid #303239;border-radius:11px;min-height:55px;padding:0 16px;gap:10px}
.mt-input-wrap label{color:#a2a3ae;font-size:14px}
.mt-input{flex:1;width:90px;min-width:50px;background:none;text-align:right;color:#f5f5f5;border:0;outline:0;font-size:22px;font-family:inherit;font-weight:750}
.mt-input:focus-visible{outline:2px solid #e1bd51;outline-offset:5px}
.mt-account{font-size:13px;color:#aaaab5;margin-top:13px}
.mt-detail{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:22px;font-size:14px}
.mt-detail+.mt-detail{margin-top:12px}
.mt-detail span{color:#92939c}
.mt-detail strong{font-size:16px;color:#efeff2}
.mt-detail.mt-large strong{font-size:28px;letter-spacing:-1px}
.mt-interest{font-size:12px;color:#a5a5ae;text-decoration:underline;text-decoration-style:dotted}
.mt-submit{width:100%;border:3px solid #b99f43;outline:2px solid #3b3521;outline-offset:2px;border-radius:32px;min-height:57px;margin-top:29px;background:#dadada;color:#151515;font-family:inherit;font-size:17px;font-weight:800;cursor:pointer}
.mt-submit:disabled{color:#666;background:#57585a;cursor:not-allowed}
.mt-flash{color:#ebbb69;font-size:12px;line-height:1.55;margin-top:12px;min-height:18px;overflow-wrap:anywhere}
.mt-footnote{color:#8a8b96;font-size:11px;margin-top:12px;line-height:1.6}
.mt-confirm{display:none;padding:23px 28px 28px;min-height:315px}
.mt-confirm.show{display:block}
.mt-confirm .mt-complete{color:#1edca7;letter-spacing:2px;font-size:13px;font-weight:900}
.mt-confirm .mt-confirm-title{font-size:22px;font-weight:800;color:#fb405e;margin:22px 0 34px}
.mt-confirm .mt-confirm-row{display:flex;justify-content:space-between;margin:9px 0;font-size:14px}
.mt-confirm .mt-confirm-row span{color:#92939c}
.mt-confirm .mt-confirm-row b{color:#f3f3f5}
.mt-done{margin-top:31px;border:0;border-radius:35px;width:100%;background:#18d79a;color:#081610;font-size:18px;font-weight:800;padding:15px;cursor:pointer}
.mt-hidden{display:none!important}
@media(max-width:650px){.manual-trade-card{max-width:none}.mt-head{padding:0 17px}.mt-inner{padding:20px 17px}.mt-market-title{font-size:19px}}
"""

SECTION = r"""
<article class="card manual-trade-card" id="manual-trader" aria-label="Manual Kalshi buy and sell">
 <div class="mt-head">
  <div class="mt-tabs" role="group" aria-label="Trade action">
   <button class="mt-tab active" id="mt-buy" type="button" aria-pressed="true">BUY</button>
   <button class="mt-tab" id="mt-sell" type="button" aria-pressed="false">SELL</button>
  </div>
  <select class="mt-selector" id="mt-unit" aria-label="Input unit">
   <option value="dollars">DOLLARS</option><option value="shares">SHARES</option>
  </select>
 </div>
 <div id="mt-main" class="mt-inner">
  <div class="mt-market"><div class="mt-btc" aria-hidden="true">₿</div>
   <div><div class="mt-market-title">BTC Up or Down - 15 minutes</div>
   <div class="mt-market-ticker" id="mt-ticker">Waiting for market…</div></div>
  </div>
  <div class="mt-directions" role="group" aria-label="Choose UP or DOWN">
   <button type="button" class="mt-side active yes" id="mt-yes" aria-pressed="true">UP —</button>
   <button type="button" class="mt-side no" id="mt-no" aria-pressed="false">DOWN —</button>
  </div>
  <div class="mt-input-wrap"><label id="mt-amount-label" for="mt-amount">Dollars</label>
   <input id="mt-amount" class="mt-input" type="number" min="0.01" step="0.01" inputmode="decimal" placeholder="$0" autocomplete="off">
  </div>
  <div class="mt-account" id="mt-account">Checking Kalshi account…</div>
  <div class="mt-detail"><span id="mt-odds-label">Odds</span><strong id="mt-odds">—</strong></div>
  <div class="mt-detail"><span id="mt-quantity-label">Estimated shares</span><strong id="mt-quantity">—</strong></div>
  <div class="mt-detail mt-large"><span id="mt-total-label">Max payout</span><strong id="mt-total">—</strong></div>
  <div class="mt-detail"><span>Estimated fee reserve</span><strong id="mt-fee">—</strong></div>
  <div class="mt-detail" id="mt-average-row"><span>Average entry price</span><strong id="mt-average">—</strong></div>
  <button class="mt-submit" type="button" id="mt-submit" disabled>⚡ Buy with 1-Click</button>
  <div class="mt-flash" id="mt-message" role="status" aria-live="polite">Waiting for active market and saved credentials.</div>
  <p class="mt-footnote">Real Kalshi orders. One click submits one immediate-or-cancel limit order at the displayed price. Actual fills, costs and fees can differ.</p>
 </div>
 <div class="mt-confirm" id="mt-confirm" role="status" aria-live="polite">
  <div class="mt-complete" id="mt-confirm-status">ORDER COMPLETED</div>
  <div class="mt-confirm-title" id="mt-confirm-title">BTC Up or Down - 15 minutes</div>
  <div class="mt-confirm-row"><span>Order</span><b id="mt-result-action">—</b></div>
  <div class="mt-confirm-row"><span>Shares filled</span><b id="mt-result-shares">—</b></div>
  <div class="mt-confirm-row"><span>Limit price</span><b id="mt-result-price">—</b></div>
  <div class="mt-confirm-row"><span>Estimated debit / credit</span><b id="mt-result-cost">—</b></div>
  <div class="mt-confirm-row"><span>Estimated fee reserve</span><b id="mt-result-fees">—</b></div>
  <div class="mt-confirm-row"><span>Kalshi order ID</span><b id="mt-result-id" style="font-size:10px;word-break:break-all;text-align:right">—</b></div>
  <button class="mt-done" type="button" id="mt-done">Done</button>
 </div>
</article>
"""

SCRIPT = r"""
<script>
(function(){
 "use strict";
 const el=id=>document.getElementById(id);
 let action="buy",side="yes",state=null,busy=false,lastTicker=null;
 const fmt=n=>Number.isFinite(n)?"$"+n.toFixed(2):"—";
 function selectedQuote(){
   return state&&state.quotes&&state.quotes[side]&&state.quotes[side][action];
 }
 function showMsg(s){el("mt-message").textContent=s||""}
 function selectAction(next){
   action=next;
   el("mt-buy").classList.toggle("active",action==="buy");
   el("mt-sell").classList.toggle("active",action==="sell");
   el("mt-buy").setAttribute("aria-pressed",String(action==="buy"));
   el("mt-sell").setAttribute("aria-pressed",String(action==="sell"));
   el("mt-unit").value=action==="sell"?"shares":"dollars";
   el("mt-amount").value="";
   render();
 }
 function selectSide(next){
   side=next;
   for(const value of ["yes","no"]){
     el("mt-"+value).classList.toggle("active",side===value);
     el("mt-"+value).setAttribute("aria-pressed",String(side===value));
   }
   render();
 }
 function render(){
   const q=selectedQuote(),val=Number(el("mt-amount").value),mode=el("mt-unit").value;
   el("mt-amount-label").textContent=mode==="dollars"?"Dollars":"Shares";
   el("mt-total-label").textContent=action==="buy"?"Max payout":"Cash out (estimate)";
   el("mt-quantity-label").textContent=action==="buy"?"Estimated shares":"Your position";
   el("mt-odds-label").textContent=action==="buy"?"Odds":"Current sell price";
   el("mt-submit").textContent="⚡ "+(action==="buy"?"Buy":"Sell")+" with 1-Click";
   const yes=state?.quotes?.yes?.buy,no=state?.quotes?.no?.buy;
   el("mt-yes").textContent="UP "+(yes?Math.round(Number(yes.price)*100)+"¢":"—");
   el("mt-no").textContent="DOWN "+(no?Math.round(Number(no.price)*100)+"¢":"—");
   el("mt-ticker").textContent=state?.ticker||"Waiting for market…";
   const balance=Number(state?.balance_dollars),position=Number(state?.position_fp);
   const held=side==="yes"?Math.max(0,position):Math.max(0,-position);
   el("mt-account").textContent=action==="buy"
     ?(Number.isFinite(balance)?"Predictions account · "+fmt(balance)+" available":"Connect Kalshi credentials to see balance")
     :(Number.isFinite(position)?"Your position: "+held.toFixed(2)+" "+(side==="yes"?"Yes":"No"):"Connect Kalshi credentials to see positions");
   const price=q?Number(q.price):NaN;
   const step=state?.fractional?0.01:1;
   let qty=Number.isFinite(val)&&val>0&&Number.isFinite(price)&&price>0
     ?(mode==="shares"?val:Math.floor((val/price)/step)*step):NaN;
   if(Number.isFinite(qty)){
     qty=Math.min(qty,Number(q.available_shares),action==="sell"?held:qty);
     qty=Math.floor((qty+1e-8)/step)*step;
   }
   const fee=Number.isFinite(price)&&Number.isFinite(qty)&&qty>0?
        Math.ceil(0.07*price*(1-price)*qty*100)/100+0.01:NaN;
   let total=qty*price;
   if(action==="buy"&&mode==="dollars"&&Number.isFinite(fee)&&Number.isFinite(val)){
     while(qty>=step&&qty*price+Math.ceil(.07*price*(1-price)*qty*100)/100+.01>val+1e-8){
       qty=+(qty-step).toFixed(2);
     }
     total=qty*price;
   }
   el("mt-odds").textContent=Number.isFinite(price)?Math.round(price*100)+"% chance":"—";
   el("mt-quantity").textContent=action==="buy"?(Number.isFinite(qty)&&qty>0?qty.toFixed(2):"—"):
      (Number.isFinite(held)?held.toFixed(2)+" shares":"—");
   el("mt-total").textContent=Number.isFinite(qty)&&qty>0?fmt(action==="buy"?qty:total):"—";
   el("mt-fee").textContent=Number.isFinite(fee)&&qty>0?fmt(fee):"—";
   el("mt-average-row").classList.toggle("mt-hidden",action!=="sell");
   el("mt-average").textContent="—"; // Average cost basis cannot be inferred reliably from net position alone.
   if(action==="sell"&&Number.isFinite(total)&&Number.isFinite(fee)&&qty>0){
     el("mt-total").textContent=fmt(Math.max(0,total-fee));
   }
   const okay=!!(state?.available&&state?.credentials_saved&&q&&
                   Number.isFinite(balance)&&Number.isFinite(position)&&
                   Number.isFinite(qty)&&qty>=step&&val>0&&!busy);
   el("mt-submit").disabled=!okay;
 }
 async function refresh(){
   if(busy || el("mt-confirm").classList.contains("show"))return;
   try{
     const response=await fetch("/api/manual/status",{cache:"no-store"});
     const value=await response.json();
     if(!response.ok)throw Error(value.error||"Quote unavailable");
     state=value;
     if(value.ticker!==lastTicker){lastTicker=value.ticker;el("mt-amount").value="";}
     if(!value.available)showMsg(value.message||"Waiting for current market");
     else if(!value.credentials_saved)showMsg("Save Kalshi API credentials in the AutoTrader panel below.");
     else if(value.message)showMsg(value.message);
     else showMsg("Ready for a manual order. Quotes refresh automatically.");
   }catch(err){state=null;showMsg("Quote unavailable: "+err.message);}
   render();
 }
 async function submit(){
   if(busy||!state?.ticker||!selectedQuote())return;
   const q=selectedQuote(),amount=el("mt-amount").value;
   if(!(Number(amount)>0))return;
   busy=true;render();
   const chosenAction=action,chosenSide=side;
   try{
     const request_id=crypto.randomUUID();
     const response=await fetch("/api/manual/order",{
       method:"POST",headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"manual"},
       body:JSON.stringify({ticker:state.ticker,action,side,mode:el("mt-unit").value,
                            amount,quoted_price:q.price,request_id})
     });
     const data=await response.json();
     if(!response.ok)throw Error(data.error||"Exchange rejected request");
     el("mt-confirm-status").textContent=data.state==="FILLED"?"ORDER COMPLETED":
       data.state==="PARTIAL"?"PARTIALLY FILLED":"NO FILL";
     el("mt-confirm-title").textContent=(chosenSide==="yes"?"Up":"Down")+" · BTC Up or Down - 15 minutes";
     el("mt-result-action").textContent=chosenAction.toUpperCase()+" "+chosenSide.toUpperCase();
     el("mt-result-shares").textContent=data.filled_shares+" of "+data.requested_shares;
     el("mt-result-price").textContent=fmt(Number(data.limit_price));
     el("mt-result-cost").textContent=fmt(Number(data.actual_total_dollars??data.indicative_cost))+(data.actual_total_dollars==null?" est.":" actual");
     el("mt-result-fees").textContent=fmt(Number(data.actual_fees_dollars??data.indicative_fees))+(data.actual_fees_dollars==null?" est.":" actual");
     el("mt-result-id").textContent=data.order_id||"—";
     el("mt-confirm").classList.add("show");
     el("mt-main").classList.add("mt-hidden");
     el("mt-amount").value="";
   }catch(err){showMsg("Trade NOT confirmed: "+err.message+" — check your Kalshi order history before retrying.");}
   finally{busy=false;render();}
 }
 el("mt-buy").addEventListener("click",()=>selectAction("buy"));
 el("mt-sell").addEventListener("click",()=>selectAction("sell"));
 el("mt-yes").addEventListener("click",()=>selectSide("yes"));
 el("mt-no").addEventListener("click",()=>selectSide("no"));
 el("mt-amount").addEventListener("input",render);
 el("mt-unit").addEventListener("change",render);
 el("mt-submit").addEventListener("click",submit);
 el("mt-done").addEventListener("click",()=>{el("mt-confirm").classList.remove("show");el("mt-main").classList.remove("mt-hidden");refresh();});
 refresh();
 setInterval(refresh,5000);
})();
</script>
"""


def integrate_manual_trade_card(page: str) -> str:
    anchor = ' <div class="context">\n'
    if page.count(anchor) != 1:
        raise ValueError("Original right-side dashboard card layout changed")
    page = page.replace(anchor, ' <div class="context manual-trade-context">\n' + SECTION + '\n', 1)
    if "</style>" not in page or "</body>" not in page:
        raise ValueError("Dashboard markup incomplete")
    page = page.replace('<span class="chip">READ-ONLY</span>', '<span class="chip">MANUAL REAL ORDERS</span>', 1)
    page = page.replace("Read-only research tool.", "Forecast research and explicitly authorized manual orders.", 1)
    page = page.replace("</style>", STYLE+"\n</style>", 1)
    page = page.replace("</body>", SCRIPT+"\n</body>", 1)
    return page
