"""Local paper AutoTrader website. No route can send a real Kalshi order."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from .paper import PaperTrader
from .vault import CredentialVault, VaultError

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KalshiEdge AutoTrader — Paper Only</title>
<style>
:root{color-scheme:dark;font:15px system-ui,Segoe UI,sans-serif;background:#09111e;color:#e6eff6}
*{box-sizing:border-box}body{margin:0}main{max-width:1060px;margin:0 auto;padding:28px 20px 90px}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:15px;border-bottom:1px solid #294153;padding-bottom:18px}
h1{font-size:27px;margin:4px 0}h2{font-size:18px;margin:0 0 16px}.muted,small{color:#91a7b8}
.badge{background:#183847;color:#89ddde;padding:7px 12px;border-radius:25px;font-weight:650}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:17px;margin-top:20px}
.card{background:#142131;border:1px solid #2c4357;border-radius:15px;padding:22px}
label{display:block;margin:13px 0 7px;color:#c1d0da;font-size:13px}
input,textarea{font:inherit;width:100%;padding:12px;border:1px solid #3b576f;border-radius:8px;background:#0d1a29;color:#f1f7fc}
textarea{min-height:125px;resize:vertical}
button{background:#82dfbc;color:#0c2232;border:0;border-radius:8px;padding:12px 15px;font:inherit;font-weight:700;cursor:pointer;margin:6px 5px 4px 0}
button.secondary{background:#263e51;color:#e8f2f5}
button.danger{background:#f2a5a8;color:#38121b}
button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid #8ddaf0}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:9px;border-bottom:1px solid #354758;text-align:left;white-space:nowrap}
.scroll{overflow:auto}.warn{color:#edc38a}.ok{color:#a1e7c8}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;color:#c1d1e2}
a{color:#8ddaf0}
</style></head><body><main>
<header><div><small>KALSHIEDGE / LOCAL EXECUTION RESEARCH</small><h1>AutoTrader</h1>
<small>Immutable V5 final forecasts · $0.10 all-in paper exposure per market</small></div>
<div class="badge">PAPER ONLY — NO LIVE ORDERS</div></header>
<p class="warn">No guarantee of profit. Paper fills use displayed liquidity and may differ from real fills. Automatic price stop-losses are disabled pending validation.</p>
<section class="grid">
<div class="card"><h2>Trading controls</h2><p id="engine">Loading...</p>
<button id="enable">Enable paper trading</button><button class="danger" id="disable">Stop new paper entries</button>
<p><small>Daily loss circuit breaker: $0.50 · Pause after 3 consecutive losses · No doubling down or late entries.</small></p></div>
<div class="card"><h2>Local Kalshi credentials</h2>
<p><small>Optional for paper mode. Stored encrypted to this Windows user via DPAPI, outside the Git repository. The paper engine never uses them to place orders.</small></p>
<form id="credentials"><label for="key">Kalshi API Key ID</label><input id="key" autocomplete="off" required maxlength="150">
<label for="pem">Private key PEM</label><textarea id="pem" autocomplete="off" required spellcheck="false" maxlength="16000"></textarea>
<button type="submit">Encrypt and save locally</button><button type="button" id="remove" class="secondary">Delete saved credentials</button></form>
<p id="credstate" class="muted">Checking credential vault...</p></div>
<div class="card"><h2>Risk status</h2><pre id="risk">Loading...</pre></div>
<div class="card"><h2>Strategy</h2>
<p>Uses only the immutable 240-second V5 forecast. A SKIP is never traded.</p>
<p>Requires a valid open KXBTC15M market, exact strike and close time, adequate opposite-side liquidity, a positive estimated net edge, and sufficient risk budget.</p>
<p>Fee estimates include a general taker-fee reserve plus a rounding buffer. Market-specific fees are not yet verified for live trading.</p></div>
</section><section class="card" style="margin-top:20px"><h2>Recent paper entries and settlements</h2><div class="scroll">
<table><thead><tr><th>Market</th><th>Forecast</th><th>Status</th><th>Qty</th><th>Price</th><th>All-in</th><th>Net P&amp;L (paper)</th><th>Reason</th></tr></thead><tbody id="trades"></tbody></table></div></section>
<p id="message" role="status" class="muted"></p>
</main><script>
const $=id=>document.getElementById(id);
const API='/api/';
async function action(path,payload){
 const response=await fetch(API+path,{method:'POST',
  headers:{'Content-Type':'application/json','X-KalshiEdge-Action':'autotrader'},
  body:JSON.stringify(payload)});
 const result=await response.json();
 if(!response.ok)throw new Error(result.error||'Action failed');
 return result;
}
function cell(row,value){
 const td=document.createElement('td');td.textContent=value===null||value===undefined?'—':String(value);row.appendChild(td);
}
async function refresh(){
 try{
 const response=await fetch(API+'status',{cache:'no-store'});
 if(!response.ok)throw new Error('Server error');
 const data=await response.json();
 $('engine').textContent=data.enabled?'Paper trading ENABLED':'New entries STOPPED';
 $('credstate').textContent=data.credentials_saved?'Encrypted credentials saved on this Windows account.':'No credentials saved.';
 $('risk').textContent=JSON.stringify(data.risk,null,2);
 $('trades').replaceChildren();
 for(const t of data.recent_attempts){
  const tr=document.createElement('tr');
  for(const k of ['ticker','forecast','status','quantity','price','debit','net_profit','reason'])cell(tr,t[k]);
  $('trades').appendChild(tr);
 }
 }catch(e){$('message').textContent='Status: '+e.message}
}
$('enable').onclick=async()=>{try{await action('paper/enable',{});$('message').textContent='Paper-only entries enabled.';refresh()}catch(e){$('message').textContent=e.message}};
$('disable').onclick=async()=>{try{await action('paper/disable',{});$('message').textContent='New paper entries stopped.';refresh()}catch(e){$('message').textContent=e.message}};
$('credentials').onsubmit=async e=>{
 e.preventDefault();
 try{await action('credentials',{api_key_id:$('key').value,private_key_pem:$('pem').value});
 $('key').value='';$('pem').value='';$('message').textContent='Key encrypted and stored locally.';refresh()}
 catch(err){$('message').textContent='Credential error: '+err.message}
};
$('remove').onclick=async()=>{try{await action('credentials/delete',{});$('message').textContent='Saved credentials deleted.';refresh()}catch(e){$('message').textContent=e.message}};
refresh();setInterval(refresh,4000);
</script></body></html>"""


def make_handler(trader: PaperTrader, vault: CredentialVault):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, code: int, data: dict, *, html: bool = False) -> None:
            payload = HTML.encode("utf-8") if html else json.dumps(data, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _local_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self._local_host():
                return self._respond(403, {"error": "invalid host"})
            if self.path == "/":
                return self._respond(200, {}, html=True)
            if self.path == "/api/status":
                data = trader.status()
                data["credentials_saved"] = vault.exists()
                return self._respond(200, data)
            if self.path == "/api/health":
                return self._respond(200, {"service": "autotrader", "mode": "paper-only"})
            return self._respond(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_host():
                return self._respond(403, {"error": "invalid host"})
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (self.headers.get("Origin") != origin
                or self.headers.get("X-KalshiEdge-Action") != "autotrader"
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")):
                return self._respond(403, {"error": "same-origin action required"})
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= 18000:
                    raise ValueError("invalid payload size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
            except (ValueError, UnicodeDecodeError):
                return self._respond(400, {"error": "invalid JSON body"})
            if self.path in ("/api/paper/enable", "/api/paper/disable"):
                if data != {}:
                    return self._respond(400, {"error": "unsupported options"})
                trader.enable(self.path.endswith("enable"))
                return self._respond(200, {"enabled": trader.enabled(), "mode": "PAPER_ONLY"})
            if self.path == "/api/credentials/delete":
                if data != {}:
                    return self._respond(400, {"error": "unsupported options"})
                vault.delete()
                return self._respond(200, {"saved": False})
            if self.path == "/api/credentials":
                if set(data) != {"api_key_id", "private_key_pem"}:
                    return self._respond(400, {"error": "invalid credential fields"})
                try:
                    vault.save(data["api_key_id"], data["private_key_pem"])
                except VaultError as exc:
                    return self._respond(400, {"error": str(exc)})
                return self._respond(200, {"saved": True})
            return self._respond(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            # Never print credential request metadata or request bodies.
            pass

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KalshiEdge paper-only AutoTrader")
    parser.add_argument("--bootstrap-root", type=Path, default=Path("data/bootstrap"))
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    trader = PaperTrader(args.bootstrap_root)
    vault = CredentialVault()
    stop = threading.Event()
    worker = threading.Thread(target=trader.run_forever, args=(stop,), daemon=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(trader, vault))
    worker.start()
    print(f"KalshiEdge AutoTrader PAPER ONLY: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        trader.enable(False)
        stop.set()
        server.server_close()
        worker.join(timeout=3)
        trader.market_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
