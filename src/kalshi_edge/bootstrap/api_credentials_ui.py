"""Minimal API credential onboarding for the localhost KalshiEdge website.

Uses the existing same-origin, DPAPI-backed credential endpoints. Credentials
are never embedded in HTML, URL parameters, storage, or analytics.
"""
from __future__ import annotations


STYLE = r"""
/* Onboarding is shown only on the /api page. */
body[data-page="api"] .layout,
body[data-page="api"] .training-card,
body[data-page="api"] .history-view{display:none!important}
body[data-page="api"] .section-intro{display:none!important}
body[data-page="api"] .api-page{display:block}
.api-page{display:none;width:min(100%,650px);margin:clamp(28px,7vh,85px) auto 40px}
.api-card{border:1px solid #30434b;border-radius:22px;
 background:linear-gradient(160deg,#16232b,#111a20);box-shadow:0 24px 75px #0005;
 padding:clamp(22px,4vw,38px)}
.api-eyebrow{font-size:10px;font-weight:800;letter-spacing:2px;color:#4adbb0;margin:0 0 15px}
.api-title{font-size:clamp(24px,3vw,33px);font-weight:800;letter-spacing:-.9px;margin:0 0 10px}
.api-intro{color:#9eb2ba;font-size:13px;line-height:1.7;margin:0 0 25px}
.api-status{border:1px solid #3a574f;background:#19332e;color:#91e3c2;border-radius:11px;
 padding:12px 14px;font-size:12px;line-height:1.5;margin-bottom:22px}
.api-status.error{border-color:#734b4e;background:#382226;color:#ff9da0}
.api-status.waiting{border-color:#53606a;background:#1d292f;color:#b5c4c9}
.api-form{display:flex;flex-direction:column;gap:10px}
.api-label{color:#d5e5e4;font-size:12px;font-weight:700}
.api-input{width:100%;border:1px solid #3d5660;border-radius:11px;padding:14px 15px;
 background:#0c171f;color:#edf9f4;font-family:inherit;font-size:13px;outline:0}
.api-input:focus{border-color:#50d8b4;box-shadow:0 0 0 3px #32c89a18}
.api-input:disabled{opacity:.5}
.api-textarea{resize:vertical;min-height:148px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.api-actions{display:flex;gap:11px;align-items:center;flex-wrap:wrap;margin-top:12px}
.api-button{border:1px solid transparent;background:#56dcb7;color:#071a17;padding:13px 17px;
 font-size:12px;font-weight:800;border-radius:10px;cursor:pointer;transition:filter .2s}
.api-button:hover:not(:disabled){filter:brightness(1.08)}
.api-button:disabled{opacity:.55;cursor:not-allowed}
.api-button.secondary{border-color:#39515b;background:#233540;color:#e1eeed}
.api-fine{color:#899da6;font-size:11px;line-height:1.65;margin:18px 0 0}
@media(max-width:650px){.api-page{margin:30px auto}.api-card{padding:22px 18px}
 .api-actions .api-button{flex:1}}
"""

MARKUP = r"""
<section class="api-page" aria-label="Kalshi API setup">
 <div class="api-card">
  <p class="api-eyebrow">ACCOUNT CONNECTION · LOCAL ONLY</p>
  <h1 class="api-title">Connect your Kalshi account</h1>
  <p class="api-intro">Enter the API Key ID and private key PEM for manual trading. Keys are encrypted with Windows DPAPI under your Windows account and are never stored in this website or GitHub.</p>
  <div id="api-status" class="api-status waiting" role="status" aria-live="polite">Checking encrypted credentials…</div>
  <form id="api-credentials-form" class="api-form" autocomplete="off">
   <label for="api-key-id" class="api-label">API Key ID</label>
   <input id="api-key-id" class="api-input" name="api-key-id" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="150" required placeholder="Your Kalshi API key ID">
   <label for="api-private-key" class="api-label">Private key PEM</label>
   <textarea id="api-private-key" class="api-input api-textarea" name="api-private-key" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="16000" required placeholder="-----BEGIN PRIVATE KEY-----"></textarea>
   <div class="api-actions">
    <button id="api-save" class="api-button" type="submit">Save encrypted locally</button>
    <button id="api-delete" class="api-button secondary" type="button" disabled>Delete saved credentials</button>
   </div>
  </form>
  <p class="api-fine">Only a valid key format is checked when saving. A stored key is not proof of an active Kalshi connection; your first live account request verifies access. You can return here anytime to update or delete credentials.</p>
 </div>
</section>
"""

SCRIPT = r"""
<script>
(function(){
 if(document.body.dataset.page!=="api")return;
 "use strict";
 const get=id=>document.getElementById(id);
 const status=get("api-status"),save=get("api-save"),del=get("api-delete");
 let busy=false,supported=false,stored=false;
 function show(message,kind){
  status.textContent=message;status.className="api-status "+(kind||"waiting");
 }
 function controls(){
  save.disabled=busy||!supported;
  del.disabled=busy||!supported||!stored;
  get("api-key-id").disabled=busy||!supported;
  get("api-private-key").disabled=busy||!supported;
 }
 async function request(path,options){
  const response=await fetch(path,{cache:"no-store",...options});
  const data=await response.json();
  if(!response.ok)throw Error(data.error||"HTTP "+response.status);
  return data;
 }
 async function refresh(){
  try{
   const info=await request("/api/autotrader/status");
   supported=!!info.credentials_supported;
   stored=!!info.credentials_saved;
   if(!supported)show("Windows encrypted credential storage is unavailable. Run KalshiEdge under your Windows user account.","error");
   else if(stored)show("Encrypted credentials are saved locally. You can return to Trade or replace the saved keys.","");
   else show("No API credentials found. Add your Kalshi API key to enable manual trading.","waiting");
  }catch(err){supported=false;stored=false;show("Could not check credential status: "+err.message,"error");}
  controls();
 }
 async function post(path,body){
  return request(path,{method:"POST",headers:{"Content-Type":"application/json","X-KalshiEdge-Action":"autotrader"},
   body:JSON.stringify(body)});
 }
 get("api-credentials-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(busy||!supported)return;
  const key=get("api-key-id").value.trim();
  const pem=get("api-private-key").value.trim();
  if(!key||!pem){show("Both the API key ID and private key PEM are required.","error");return}
  busy=true;controls();show("Encrypting credentials locally…","waiting");
  try{
   const result=await post("/api/autotrader/credentials",{api_key_id:key,private_key_pem:pem});
   if(!result.saved)throw Error("Credential storage was not confirmed");
   get("api-key-id").value="";get("api-private-key").value="";
   stored=true;show("Credentials saved. Opening Trade…","");
   window.location.assign("/trade");
  }catch(err){
   show("Could not save credentials: "+err.message,"error");
  }finally{busy=false;controls();}
 });
 del.addEventListener("click",async()=>{
  if(busy||!supported||!stored)return;
  if(!confirm("Delete your locally encrypted Kalshi credentials? This will disconnect manual trading."))return;
  busy=true;controls();show("Deleting encrypted credentials…","waiting");
  try{
   const result=await post("/api/autotrader/credentials/delete",{});
   if(result.saved!==false)throw Error("Deletion was not confirmed");
   stored=false;get("api-key-id").value="";get("api-private-key").value="";
   show("Credentials deleted. Enter a new key to reconnect.","waiting");
  }catch(err){show("Could not delete credentials: "+err.message,"error");}
  finally{busy=false;controls();}
 });
 refresh();
})();
</script>
"""


def integrate_api_credentials(page: str) -> str:
    """Embed form in the shared shell, without affecting other page scripts."""
    before = '<div class="section-intro">'
    if page.count(before) != 1 or page.count("</style>") != 1 or page.count("</body>") != 1:
        raise ValueError("API page insertion points changed")
    page = page.replace("</style>", STYLE + "\n</style>", 1)
    page = page.replace(before, MARKUP + "\n" + before, 1)
    page = page.replace("</body>", SCRIPT + "\n</body>", 1)
    return page
