"""Inline AutoTrader panel for the existing KalshiEdge dashboard.

No separate port, iframe, redirect or JavaScript application is required.
"""
from __future__ import annotations

STYLE = r"""
/* Integrated AutoTrader uses the main dashboard's typography and card palette. */
.auto-card{padding:24px 26px;margin-top:24px;border-top:2px solid var(--mint)}
.auto-header{display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap}
.auto-header h2{font-size:24px;letter-spacing:-.7px;margin:6px 0}
.auto-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px;margin-top:21px}
.auto-panel{padding:19px;border:1px solid var(--line);border-radius:12px;background:#10202f}
.auto-panel h3{font-size:15px;margin:0 0 10px}
.auto-panel p{font-size:12px;line-height:1.7;color:var(--muted)}
.auto-field{display:block;font-size:12px;color:#c6d7df;margin:12px 0 7px}
.auto-input{display:block;width:100%;border:1px solid #35536b;border-radius:8px;background:#091825;color:var(--text);padding:11px;font:inherit;font-size:12px}
textarea.auto-input{min-height:126px;resize:vertical}
.auto-btn{border:0;background:#86e2c3;color:#102735;padding:11px 14px;margin:8px 6px 4px 0;font:inherit;font-size:12px;font-weight:750;border-radius:8px;cursor:pointer}
.auto-btn.alt{background:#284258;color:var(--text)}
.auto-btn.danger{background:#e6a0ab;color:#3b1523}
.auto-btn:focus-visible,.auto-input:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
.auto-notice{color:var(--amber);font-size:12px;line-height:1.7;margin:12px 0}
.auto-status{font-size:12px;color:var(--muted);min-height:22px;line-height:1.6}
.auto-risk{font-variant-numeric:tabular-nums;white-space:pre-wrap;word-break:break-word;color:#c3d8e1;font-size:12px}
.auto-table{overflow:auto;max-height:420px}
.auto-table table{width:100%;border-collapse:collapse;white-space:nowrap}
.auto-table th,.auto-table td{padding:11px 10px;border-bottom:1px solid #283d50;text-align:left;font-size:11px}
@media(max-width:750px){.auto-grid{grid-template-columns:1fr}.auto-card{padding:18px}.auto-panel{padding:15px}}
"""

SECTION = r"""
<section id="autotrader" class="card auto-card" aria-label="AutoTrader — integrated paper-trading controls">
 <div class="auto-header">
  <div><div class="eyebrow">EXECUTION RESEARCH / BUILT-IN</div><h2>AutoTrader</h2>
  <p class="subtitle">Uses the same locked V5 forecast and official settlement history, on this website.</p></div>
  <span class="chip">PAPER ONLY · NO LIVE ORDERS</span>
 </div>
 <p class="auto-notice">A $0.10 simulated entry can lose the full amount. Price-stop exits are not active; simulated fills, fees and performance are not verified live results.</p>
 <div class="auto-grid">
  <div class="auto-panel">
   <h3>Automatic paper trading</h3>
   <div id="auto-engine-state" class="auto-status">Checking engine…</div>
   <button type="button" class="auto-btn" id="auto-enable">Enable paper trading</button>
   <button type="button" class="auto-btn danger" id="auto-disable">Stop new entries</button>
   <p>Maximum estimated debit: <strong>$0.10 per market</strong> · Daily paper risk limit: <strong>$0.50</strong> · Pause after 3 losses. Final forecast SKIP is never traded.</p>
  </div>
  <div class="auto-panel">
   <h3>Risk status</h3>
   <div id="auto-risk-state" class="auto-risk">Loading…</div>
   <p>Trade only when there is sufficient public orderbook liquidity and estimated positive edge after fee reserve. No martingale or automatic direction reversal.</p>
  </div>
  <div class="auto-panel">
   <h3>Kalshi API credentials</h3>
   <p>Optional in paper mode. Encrypt and save to your Windows account outside this repository. Credentials are not used for placing orders.</p>
   <form id="autotrader-credentials" autocomplete="off">
    <label class="auto-field" for="auto-key">API Key ID</label>
    <input class="auto-input" id="auto-key" required maxlength="150" autocomplete="off" spellcheck="false">
    <label class="auto-field" for="auto-pem">Private key PEM</label>
    <textarea class="auto-input" id="auto-pem" required maxlength="16000" autocomplete="off" spellcheck="false"></textarea>
    <button type="submit" class="auto-btn">Save encrypted locally</button>
    <button type="button" class="auto-btn alt" id="auto-delete-key">Delete saved credentials</button>
   </form>
   <div id="auto-key-state" class="auto-status" role="status">Checking saved credentials…</div>
  </div>
  <div class="auto-panel">
   <h3>Strategy and stop-loss</h3>
   <p>Entries depend only on the immutable V5 final 240-second lock (ABOVE/BELOW). Missing, late or SKIP decisions are excluded.</p>
   <p>Initial loss protection limits each market's potential stake, total daily paper exposure and consecutive losses. A fixed percentage sell-stop has not demonstrated better net returns, so it is not enabled.</p>
   <p>This panel is the existing KalshiEdge application's built-in trading research, not an external site.</p>
  </div>
 </div>
 <div class="auto-panel" style="margin-top:17px">
  <h3>Paper trade history and simulated P&amp;L</h3>
  <div class="auto-table"><table>
   <thead><tr><th>Market</th><th>Forecast</th><th>Status</th><th>Quantity</th><th>Price</th><th>Estimated cost</th><th>Paper P&amp;L</th><th>Reason</th></tr></thead>
   <tbody id="auto-trade-rows"></tbody>
  </table></div>
 </div>
 <div id="auto-action-message" class="auto-status" role="status" aria-live="polite"></div>
</section>
"""

SCRIPT = r"""
<script>
(function(){
 const el=id=>document.getElementById(id);
 const prefix='/api/autotrader/';
 let busy=false;
 async function request(path, data){
   const response=await fetch(prefix+path,{
     method:'POST',
     headers:{'Content-Type':'application/json','X-KalshiEdge-Action':'autotrader'},
     body:JSON.stringify(data)
   });
   const value=await response.json();
   if(!response.ok)throw Error(value.error||'Action rejected');
   return value;
 }
 function field(row,value){
   const td=document.createElement('td');
   td.textContent=value===null||value===undefined?'—':String(value);
   row.appendChild(td);
 }
 async function refresh(){
   if(busy)return;
   try{
    const response=await fetch(prefix+'status',{cache:'no-store'});
    if(!response.ok)throw Error('AutoTrader status unavailable');
    const status=await response.json();
    el('auto-engine-state').textContent=status.enabled?'Paper trading ENABLED':'New paper entries STOPPED';
    el('auto-key-state').textContent=status.credentials_saved?'Encrypted credentials stored locally':(
       status.credentials_supported===false?'Windows credential vault unavailable':'No credentials saved');
    el('auto-risk-state').textContent=JSON.stringify(status.risk,null,2);
    el('auto-trade-rows').replaceChildren();
    for(const trade of status.recent_attempts||[]){
      const tr=document.createElement('tr');
      for(const key of ['ticker','forecast','status','quantity','price','debit','net_profit','reason']){
       field(tr,trade[key]);
      }
      el('auto-trade-rows').appendChild(tr);
    }
   }catch(err){el('auto-engine-state').textContent='Status unavailable: '+err.message}
 }
 async function act(path,data,success){
   busy=true;
   try{
    await request(path,data);
    el('auto-action-message').textContent=success;
   }catch(err){
    el('auto-action-message').textContent='Action failed: '+err.message;
   }finally{
    busy=false;
    refresh();
   }
 }
 el('auto-enable').addEventListener('click',()=>act('paper/enable',{},'Paper mode enabled. No real orders can be placed.'));
 el('auto-disable').addEventListener('click',()=>act('paper/disable',{},'New paper entries stopped.'));
 el('auto-delete-key').addEventListener('click',()=>act('credentials/delete',{},'Saved credentials deleted.'));
 el('autotrader-credentials').addEventListener('submit',event=>{
   event.preventDefault();
   const value={api_key_id:el('auto-key').value,private_key_pem:el('auto-pem').value};
   el('auto-key').value='';el('auto-pem').value='';
   act('credentials',value,'Credentials encrypted and saved on this Windows account.');
 });
 refresh();
 setInterval(refresh,4000);
})();
</script>
"""


def integrated_page(page: str) -> str:
    """Add AutoTrader to the existing page without altering other dashboards."""
    if '<section class="card training-card"' not in page:
        raise RuntimeError("main dashboard insertion point missing")
    page = page.replace(
        'href="http://127.0.0.1:8898/" title="Open local paper-only AutoTrader (start it separately)"',
        'href="#autotrader" title="Jump to the AutoTrader panel on this page"',
        1
    )
    page = page.replace("</style>", STYLE + "\n</style>", 1)
    page = page.replace('<section class="card training-card"', SECTION + '\n<section class="card training-card"', 1)
    page = page.replace("</body>", SCRIPT + '\n</body>', 1)
    return page
