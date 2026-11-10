"""Train navigation restores measured progress immediately, then revalidates.

The actual routes intentionally cause document reloads. The first render must
re-use tab-session status without re-running or resetting any training worker.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.site_pages import render_site_page


def _train_html() -> str:
    # This test covers the actual navigable route, not the unrouted template.
    from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero

    return render_site_page(
        integrate_api_credentials(integrate_manual_trade(
            integrate_round_price_hero(PAGE)
        )),
        "train",
    )


def _train_script() -> str:
    scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", _train_html())
    matching = [script for script in scripts if "async function refreshTraining()" in script]
    assert len(matching) == 1
    return matching[0]


def test_train_route_restores_last_progress_before_background_network_refresh():
    script = _train_script()
    assert "function restoreTrainingSnapshot()" in script
    assert "function saveTrainingSnapshot(info)" in script
    assert 'sessionStorage.getItem(TRAIN_STATUS_CACHE_KEY)' in script
    assert 'sessionStorage.setItem(TRAIN_STATUS_CACHE_KEY' in script
    assert 'renderTraining(snapshot.status)' in script
    assert 'saveTrainingSnapshot(info)' in script
    # Re-render immediately before any network promise can resolve.
    assert 'restoreTrainingSnapshot();refreshTraining();' in script
    assert script.index("restoreTrainingSnapshot();refreshTraining();") < script.index(
        "setInterval(refreshTraining,5000)"
    )
    assert 'if(trainingStatus)' in script
    assert '"Training readiness unavailable"' in script  # still accurate on cold-load failure


def test_train_session_snapshot_hydration_is_immediate_but_unconfirmed(tmp_path):
    """Exercise actual JS helpers with Node if available; never just assert strings."""
    node = shutil.which("node")
    if node is None:
        return  # Python-only environments still run the structural regression.
    script = _train_script()
    begin = script.index("const TRAIN_STATUS_CACHE_KEY=")
    end = script.index("function renderTraining(info)", begin)
    helpers = script[begin:end]
    js = (
        'const store=new Map();let renders=[];let messages=[];let disabled=false;\n'
        'const sessionStorage={getItem:(k)=>store.get(k)||null,'
        'setItem:(k,v)=>store.set(k,v),removeItem:(k)=>store.delete(k)};\n'
        'function renderTraining(s){renders.push(s)};\n'
        'function el(id){if(id!=="train-now")throw Error("bad element");'
        'return {set disabled(v){disabled=v}, get disabled(){return disabled}}};\n'
        'function text(id,value){messages.push([id,value]);};\n'
        + helpers + "\n"
        + 'saveTrainingSnapshot({run:{stage:"BUILDING_DATASET",'
          'progress_percent:8,dataset_markets_completed:173,'
          'dataset_markets_total:472},current_model:{model_number:2},running:true});\n'
        + 'if(restoreTrainingSnapshot()!==true)throw Error("did not restore");\n'
        + 'if(renders.length!==1||renders[0].run.dataset_markets_completed!==173)'
          'throw Error("restored incorrect dataset progress");\n'
        + 'if(!disabled)throw Error("cached status authorized new training");\n'
        + 'const key=TRAIN_STATUS_CACHE_KEY;store.set(key,"{invalid json");\n'
        + 'if(restoreTrainingSnapshot()!==false)throw Error("bad cache accepted");\n'
        + 'if(renders.length!==1)throw Error("invalid cache reset progress");\n'
        + 'console.log("hydrated progress 173/472; safe stale-check; corrupt cache ignored");\n'
    )
    result = subprocess.run(
        [node, "-"], input=js, text=True, capture_output=True,
        timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "hydrated progress 173/472" in result.stdout


def test_train_route_javascript_remains_valid_after_hydration():
    node = shutil.which("node")
    if node is None:
        return
    result = subprocess.run(
        [node, "--check"], input=_train_script(), text=True,
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
