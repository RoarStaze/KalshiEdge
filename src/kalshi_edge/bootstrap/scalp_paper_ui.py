"""Append a clearly separate paper-only scalper panel inside AutoTrade view.

The existing live AutoTrade buttons and routes are unchanged. Every paper
action uses the separate localhost paper-only endpoints.
"""

PANEL = r"""
   <section id="paper-scalp-panel" class="mt-paper-scalp" aria-label="Paper-only intramarket scalper">
    <div class="mt-paper-head"><strong>PAPER SCALPER</strong><span>PAPER ONLY · SIMULATED</span></div>
    <p class="mt-paper-warning">Virtual trades only. No real orders, no guaranteed fills or stops. Hypothetical fees and 1¢ slippage per share on each side.</p>
    <div class="mt-paper-controls">
     <label for="paper-scalp-amount">Paper budget per entry ($)</label>
     <input id="paper-scalp-amount" type="number" min="0.10" max="5" step="0.01" value="1.00" inputmode="decimal">
     <button type="button" id="paper-scalp-toggle">Start Paper Scalper</button>
    </div>
    <div class="mt-paper-status" id="paper-scalp-status" role="status">Paper scalper inactive · virtual capital only</div>
    <div id="paper-scalp-results" class="mt-paper-results" aria-live="polite">No paper scalp cycles recorded.</div>
   </section>
"""

STYLE = r"""
.mt-paper-scalp{margin:14px 0 0;padding:12px 13px;border:1px solid #496c70;
 border-radius:11px;background:#102a30;color:#dfebed;font-size:11px}
.mt-paper-head{display:flex;gap:9px;justify-content:space-between;align-items:center;
 letter-spacing:1px;color:#a3f3ec;flex-wrap:wrap}
.mt-paper-head span{font-size:9px;color:#f7ca80;letter-spacing:.5px}
.mt-paper-warning{margin:8px 0;line-height:1.5;color:#c5d9dc}
.mt-paper-controls{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.mt-paper-controls input{width:85px;padding:7px;background:#0e1a24;color:white;
 border:1px solid #557380;border-radius:7px}
.mt-paper-controls button{padding:8px 12px;border:1px solid #5ecfbc;
 border-radius:9px;background:#173e44;color:#acf4e8;font-weight:700;cursor:pointer}
.mt-paper-controls button:disabled{opacity:.55}
.mt-paper-status{margin:9px 0;line-height:1.5;color:#e1e9ef;overflow-wrap:anywhere}
.mt-paper-results{max-height:180px;overflow-y:auto;font-size:10px;line-height:1.6}
.mt-paper-result{border-top:1px solid #36535b;padding:6px 0}
"""

SCRIPT = r"""
<script>
(function(){
 "use strict";
 const panel=document.getElementById("paper-scalp-panel");
 if(!panel)return;
 const byId=id=>document.getElementById(id);
 const btn=byId("paper-scalp-toggle");
 const amount=byId("paper-scalp-amount");
 const status=byId("paper-scalp-status");
 const results=byId("paper-scalp-results");
 let current=null,busy=false;
 function dollars(value){
  return typeof value==="string"&&/^-?\d+(\.\d+)?$/.test(value)
   ?(Number(value)<0?"−":"")+"$"+Math.abs(Number(value)).toFixed(2):"—";
 }
 function render(){
  const state=current;
  btn.disabled=busy||!state;
  btn.textContent=state&&state.enabled?"Pause Paper Entries":"Start Paper Scalper";
  amount.disabled=busy||!!(state&&state.enabled);
  if(!state)return;
  const o=state.open_position;
  const risk=o?" · OPEN "+o.side+" "+o.quantity+" shares ("+o.ticker+")":" · FLAT";
  status.textContent=(state.enabled?"PAPER ARMED":"PAPER PAUSED")+
   risk+" · virtual cash "+dollars(state.virtual_cash_dollars)+
   " · paper net "+dollars(state.simulated_net_pnl_dollars)+
   " · "+String(state.last_signal||"WAITING").replaceAll("_"," ");
  results.replaceChildren();
  const cycles=Array.isArray(state.recent_cycles)?state.recent_cycles.slice(0,12):[];
  if(cycles.length===0){results.textContent="No paper scalp cycles recorded.";return}
  for(const c of cycles){
   const row=document.createElement("div");
   row.className="mt-paper-result";
   const pnl=c.status==="OPEN"?"open, unrealized":"paper P/L "+dollars(c.paper_net_pnl);
   row.textContent="#"+c.id+" "+c.side+" · "+c.quantity+" shares · "+
    (c.status==="OPEN"?"OPEN":String(c.exit_reason||c.status).replaceAll("_"," "))+
    " · "+pnl;
   results.appendChild(row);
  }
 }
 async function request(path,body){
  const options={cache:"no-store"};
  if(body!==undefined){
   options.method="POST";
   options.headers={"Content-Type":"application/json",
    "X-KalshiEdge-Action":"paper-scalp"};
   options.body=JSON.stringify(body);
  }
  const response=await fetch("/api/paper-scalp/"+path,options);
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||"Paper-only service unavailable");
  return data;
 }
 async function refresh(){
  if(busy)return;
  try{current=await request("status");render()}
  catch(e){status.textContent="Paper-only status unavailable: "+e.message}
 }
 btn.addEventListener("click",async()=>{
  if(busy||!current)return;
  const armed=current.enabled;
  const raw=amount.value.trim();
  if(!armed&&(!/^\d+(\.\d{1,2})?$/.test(raw)||Number(raw)<.10)){
   status.textContent="Enter a valid paper budget of at least $0.10";return
  }
  busy=true;render();
  try{
   current=await request(armed?"pause":"arm",armed?{}:{amount:raw});
   render();
  }catch(e){status.textContent="Paper action refused: "+e.message}
  finally{busy=false;render()}
 });
 refresh();
 setInterval(refresh,5000);
})();
</script>
"""


def integrate_paper_scalp(page: str) -> str:
    """Integrate once after manual AutoTrade markup has been constructed."""
    anchor = ('<div class="mt-auto-history-list" id="manual-auto-history-list" '
              'aria-live="polite"><div class="mt-auto-empty">'
              'No AutoTrades recorded yet.</div></div>')
    if (page.count(anchor) != 1 or page.count("</style>") < 1
            or page.count("</body>") != 1
            or 'id="paper-scalp-panel"' in page):
        raise ValueError("Paper scalp integration requires one intact Trade template")
    page = page.replace(anchor, anchor + PANEL, 1)
    page = page.replace("</style>", STYLE + "\n</style>", 1)
    return page.replace("</body>", SCRIPT + "\n</body>", 1)
