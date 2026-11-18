"""Manual SELL must remain navigable while continuous AutoTrade is armed.

These tests exercise the actual browser JavaScript snippets in Node, without
any exchange connectivity or order submission.
"""
import shutil
import subprocess

import pytest
from kalshi_edge.bootstrap.manual_trade_ui import SCRIPT


def _run(script):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    process = subprocess.run(["node", "-"], input=script, text=True,
                             capture_output=True, timeout=15)
    assert process.returncode == 0, process.stderr


def test_sell_navigation_does_not_cancel_armed_auto():
    start = SCRIPT.index("function setMode(next){")
    end = SCRIPT.index("function setSide(next){", start)
    body = SCRIPT[start:end]
    _run(r"""
const nodes=new Proxy({}, {get:(target,id)=>{
 if(!target[id])target[id]={classList:{toggle:()=>{}},value:"DOLLARS",disabled:false,textContent:""};
 return target[id];
}});
const byId=id=>nodes[id];
const shown=(id,value)=>{byId(id).textContent=String(value)};
let action="BUY",autoSelected=true,placing=false,buyUnits="DOLLARS";
const autoPending=()=>true;
const renderAuto=()=>{},renderHolding=()=>{},refreshPreview=()=>{};
""" + body + r"""
setMode("SELL");
if(action!=="SELL")throw Error("SELL tab blocked by armed AUTO");
if(autoSelected)throw Error("SELL tab must be separate from AUTO selection");
setMode("BUY");
if(action!=="BUY"||!autoSelected)throw Error("Armed AUTO not restored");
""")


def test_autotrade_button_displays_current_bought_and_confirmed_sold_status():
    start = SCRIPT.index("const compactPnl=v=>")
    end = SCRIPT.index("function renderAutoPayout(){",start)
    snippets = SCRIPT[start:end]
    _run(r"""
const node={className:"",textContent:"",classList:{toggle:()=>{}}};
const byId=id=>node;
let action="BUY",autoSelected=true,snapshot=null;
""" + snippets + r"""
snapshot={ticker:"KXBTC15M-TEST",position_yes:"0.40",
          pnl_status:"ESTIMATED",unrealized_pnl_dollars:"0.0420"};
renderAutoPosition();
if(!node.textContent.includes("BOUGHT UP · UNREALIZED P/L +$0.0420"))
 throw Error("Holding status unavailable below AUTO");
snapshot.position_yes="0.0000";
snapshot.last_sell={status:"SOLD",ticker:"KXBTC15M-TEST",
                   realized_pnl_dollars:"-0.0074"};
renderAutoPosition();
if(!node.textContent.includes("SOLD · REALIZED P/L −$0.0074"))
 throw Error("Confirmed sale status unavailable below AUTO");
""")
