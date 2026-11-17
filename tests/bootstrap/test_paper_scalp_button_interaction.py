"""Regression: PAPER SCALPER Start must never silently discard a click.

Execute the exact browser script in a small Node DOM/fetch harness, not just
a JS syntax check. No live Kalshi requests or real orders are made.
"""
import json
import re
import shutil
import subprocess

import pytest

from kalshi_edge.bootstrap.scalp_paper_ui import SCRIPT


NODE = shutil.which("node")
PAPER_JS = re.search(r"<script>([\s\S]*?)</script>", SCRIPT).group(1)


def interact(mode: str) -> dict:
    if NODE is None:
        pytest.skip("Node is required to execute the browser click handler")
    harness = r"""
const vm = require("node:vm");
const source = JSON.parse(process.argv[1]);
const mode = process.argv[2];
const elements = new Map();
const requests = [];
function node(id){
  if(!elements.has(id)){
    elements.set(id,{
      id, value:id==="paper-scalp-amount"?"1.00":"",
      disabled:false,textContent:"",listeners:{},childNodes:[],
      addEventListener(kind,fn){this.listeners[kind]=fn},
      replaceChildren(){this.childNodes=[]},
      appendChild(child){this.childNodes.push(child)}
    });
  }
  return elements.get(id);
}
const document={
 getElementById:node,
 createElement:(tag)=>node("created-"+tag+"-"+elements.size),
};
let reads=0;
async function fetch(url,opts){
  requests.push({url,method:opts&&opts.method||"GET"});
  if(url.endsWith("/status")){
    reads += 1;
    if(mode==="outage" || (mode==="transient" && reads===1)){
      return {ok:false,status:503,json:async()=>({error:"Paper scalp simulator unavailable"})};
    }
    return {ok:true,status:200,json:async()=>({
      mode:"PAPER_SCALP_SIMULATED",enabled:false,live_order_submission:false,
      virtual_cash_dollars:"100.00",simulated_net_pnl_dollars:"0",
      last_signal:"IDLE",recent_cycles:[],open_position:null
    })};
  }
  if(url.endsWith("/arm")){
    if(mode==="rejectpost"){
      return {ok:false,status:403,
        json:async()=>({error:"Same-origin paper action required"})};
    }
    return {ok:true,status:200,json:async()=>({
      mode:"PAPER_SCALP_SIMULATED",enabled:true,live_order_submission:false,
      virtual_cash_dollars:"100.00",simulated_net_pnl_dollars:"0",
      last_signal:"WAITING_FOR_FEE_ADJUSTED_ENTRY",recent_cycles:[],open_position:null
    })};
  }
  throw Error("Unexpected route "+url);
}
vm.runInNewContext(source,{document,fetch,setInterval(){}});
(async()=>{
  // Run microtasks queued by the startup GET.
  await new Promise(resolve=>setImmediate(resolve));
  const button=node("paper-scalp-toggle");
  if(!button.listeners.click)throw Error("No Start click listener installed");
  await button.listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  process.stdout.write(JSON.stringify({
    status:node("paper-scalp-status").textContent,
    label:button.textContent,
    disabled:button.disabled,
    reads,requests
  }));
})().catch(err=>{console.error(err);process.exitCode=1});
"""
    proc = subprocess.run(
        [NODE, "-e", harness, json.dumps(PAPER_JS), mode],
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_start_click_reports_missing_backend_instead_of_silent_return():
    result = interact("outage")
    assert result["reads"] >= 2, result
    assert "Cannot start paper scalper" in result["status"]
    assert "unavailable" in result["status"].lower()
    assert not any(r["method"] == "POST" for r in result["requests"])


def test_start_click_recovers_from_one_failed_status_request():
    result = interact("transient")
    assert result["reads"] >= 2, result
    assert any(r["url"].endswith("/arm") and r["method"] == "POST"
               for r in result["requests"]), result
    assert result["label"] == "Pause Paper Entries"
    assert "PAPER ARMED" in result["status"]


def test_start_with_working_backend_arms_paper_only_without_real_trade_route():
    result = interact("healthy")
    assert any(r["url"].endswith("/arm") and r["method"] == "POST"
               for r in result["requests"]), result
    assert all("/api/paper-scalp/" in r["url"] for r in result["requests"])
    assert "PAPER ARMED" in result["status"]


def test_rejected_arm_request_stays_visible_instead_of_ending_as_paper_paused():
    result = interact("rejectpost")
    assert "Paper action refused" in result["status"], result
    assert "Same-origin paper action required" in result["status"]
    assert result["label"] == "Start Paper Scalper"
