"""Training page must show ACTIVE stage, not the previous dataset counter."""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from kalshi_edge.bootstrap import training_cycle
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.site_pages import render_site_page
from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials


def test_worker_publishes_actual_fit_operations_and_current_action(tmp_path):
    publisher=training_cycle._fit_progress_reporter(
        tmp_path, mode="baseline", round_number=0)
    publisher(0, 20, "Preparing verified training partitions")
    state=training_cycle._run_state(tmp_path)
    assert state["stage"]=="TRAINING"
    assert state["fit_steps_completed"]==0
    assert state["fit_steps_total"]==20
    assert "partitions" in state["fit_step_detail"].lower()

    publisher(1,20,"Fitted logistic fold 1")
    publisher(2,20,"Fitted histogram fold 1")
    state=training_cycle._run_state(tmp_path)
    assert state["fit_steps_completed"]==2
    assert state["fit_steps_total"]==20
    assert "histogram" in state["fit_step_detail"].lower()
    assert state["progress_percent"]<100


def test_fit_progress_refuses_fabricated_counts(tmp_path):
    publisher=training_cycle._fit_progress_reporter(
        tmp_path,mode="baseline",round_number=0)
    with pytest.raises(ValueError):
        publisher(25,20,"too many steps")
    with pytest.raises(ValueError):
        publisher(-1,20,"negative")
    with pytest.raises(ValueError):
        publisher(1,0,"unknown")
    assert training_cycle._run_state(tmp_path) is None


def test_page_uses_active_stage_and_never_reuses_dataset_count_after_stage1():
    assert 'function stageProgressState(run)' in PAGE
    assert 'if(display.mode==="dataset")' in PAGE
    assert 'if(display.mode==="fit"&&display.total!==null)' in PAGE
    assert 'id="dataset-build-detail"' in PAGE
    assert 'id="dataset-build-bar"' in PAGE
    assert 'run.fit_steps_completed' in PAGE
    assert 'run.fit_step_detail' in PAGE
    assert 'run.stage==="BUILDING_DATASET"' in PAGE
    assert 'stageProgressState(run)' in PAGE
    assert 'datasetTicker.raf' in PAGE

    node=shutil.which("node")
    if node is None:
        return
    match=re.search(r"function stageProgressState\(run\)\s*\{.*?^\}",PAGE,re.S|re.M)
    assert match is not None
    script=match.group(0)+"""
const stale={dataset_markets_completed:29865,dataset_markets_total:29865,
 dataset_progress_phase:"markets",dataset_action:"Rebuilding verified historical data",
 fit_steps_completed:4,fit_steps_total:22,fit_step_detail:"Fitted logistic model"};
const fit=stageProgressState({...stale,stage:"TRAINING"});
if(fit.mode!=="fit"||fit.completed!==4||fit.total!==22||
 !fit.detail.includes("logistic")||fit.detail.includes("historical data"))
 throw Error("stage4 reused stale dataset progress: "+JSON.stringify(fit));
const scoring=stageProgressState({...stale,stage:"SCORING"});
if(scoring.mode!=="stage"||scoring.label!=="Score holdout"||
 scoring.detail.includes("historical data"))
 throw Error("stage5 reused dataset progress: "+JSON.stringify(scoring));
const dataset=stageProgressState({...stale,stage:"BUILDING_DATASET"});
if(dataset.mode!=="dataset")throw Error("dataset counter lost: "+JSON.stringify(dataset));
const waiting=stageProgressState({stage:"FITTING"});
if(waiting.mode!=="fit"||waiting.total!==null)
 throw Error("invented fit progress: "+JSON.stringify(waiting));
"""
    p=subprocess.run([node,"-"],input=script,text=True,capture_output=True,timeout=10)
    assert p.returncode==0,p.stderr


def test_fast_progress_endpoint_and_stage_pages_share_stable_ids():
    page=render_site_page(
        integrate_api_credentials(integrate_manual_trade(
            integrate_round_price_hero(PAGE))),"train")
    assert 'setInterval(refreshTrainingProgress,250)' in page
    assert '"/api/training/progress"' in page
    assert 'id="training-worker-progress"' in page
    assert 'id="dataset-build-detail"' in page
    assert 'id="dataset-build-bar"' in page
    assert 'id="train-pause"' in page
    assert 'id="train-resume"' in page


def test_dashboard_full_javascript_remains_valid_after_stage_switch():
    node = shutil.which("node")
    if node is None:
        return
    scripts = re.findall(r"<script>(.*?)</script>", PAGE, re.S)
    assert scripts
    for script in scripts:
        p = subprocess.run(
            [node, "--check"], input=script, text=True,
            capture_output=True, timeout=10,
        )
        assert p.returncode == 0, p.stderr
