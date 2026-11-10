"""Pause/resume training from verified checkpoints, including app restarts.

No real orders or production Kalshi API; tests exercise durable control and
single-worker safety independently of user credentials.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from kalshi_edge.bootstrap import training_cycle
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.final_history import _make_handler


def test_paused_worker_restarts_after_resume_without_erasing_progress(tmp_path):
    root = tmp_path
    training_cycle._write_worker_state(root, stage="BUILDING_DATASET",
        mode="baseline", round_number=0, dataset_markets_completed=125,
        dataset_markets_total=1000, auto_resume=True, git_sha="a"*40)
    session=training_cycle._run_state(root)["run_id"]
    training_cycle._set_pause_request(root, session, True)
    seen=[]
    def waiter():
        training_cycle._pause_barrier(root, stage="BUILDING_DATASET",
            mode="baseline", round_number=0)
        seen.append("continued")
    thread=threading.Thread(target=waiter)
    thread.start()
    try:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if training_cycle._run_state(root).get("stage")=="PAUSED":
                break
            time.sleep(.02)
        state=training_cycle._run_state(root)
        assert state["stage"]=="PAUSED"
        assert state["dataset_markets_completed"]==125
        assert not seen
        training_cycle._set_pause_request(root, session, False)
        thread.join(timeout=3)
        assert seen==["continued"]
        assert training_cycle._run_state(root)["stage"]=="BUILDING_DATASET"
        assert training_cycle._run_state(root)["dataset_markets_completed"]==125
    finally:
        training_cycle._set_pause_request(root, session, False)
        thread.join(timeout=3)


def test_pause_request_requires_matching_run_id(tmp_path):
    root=tmp_path
    training_cycle._write_worker_state(root, stage="BUILDING_DATASET",
        mode="baseline",round_number=0)
    with pytest.raises(training_cycle.CycleError):
        training_cycle._set_pause_request(root, "wrong-session", True)


def test_pause_is_checked_before_publishing_each_market(tmp_path,monkeypatch):
    root=tmp_path
    training_cycle._write_worker_state(root,stage="BUILDING_DATASET",
       mode="baseline",round_number=0, dataset_markets_completed=17)
    session=training_cycle._run_state(root)["run_id"]
    seen=[]
    monkeypatch.setattr(training_cycle, "_pause_barrier",
        lambda *args,**kwargs: seen.append(kwargs["stage"]))
    progress=training_cycle._market_progress_reporter(root,mode="baseline",round_number=0)
    progress(18,100,"test","markets")
    assert seen==["BUILDING_DATASET"]
    assert training_cycle._run_state(root)["dataset_markets_completed"]==18


def test_controller_pause_resume_changes_persistent_control_and_not_dataset(tmp_path):
    c=training_cycle.TrainingController(tmp_path, project_root=tmp_path)
    training_cycle._write_worker_state(tmp_path,stage="BUILDING_DATASET",
        mode="baseline",round_number=0,auto_resume=True,
        dataset_markets_completed=250,dataset_markets_total=1000)
    class Running:
        pid=900
        def poll(self):return None
    c._process=Running()
    p=c.pause()
    assert p["pause_requested"] is True
    assert c.progress()["run"]["dataset_markets_completed"]==250
    assert c.progress()["pause_requested"] is True
    resumed=c.resume()
    assert resumed["pause_requested"] is False
    assert c.progress()["pause_requested"] is False


def test_auto_recovery_launches_same_run_without_user_action(tmp_path,monkeypatch):
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(tmp_path,stage="PAUSED",
        resume_stage="BUILDING_DATASET",mode="baseline",round_number=0,
        git_sha="b"*40,auto_resume=True,dataset_markets_completed=573,
        dataset_markets_total=2000)
    called=[]
    monkeypatch.setattr(controller,"_launch",
        lambda *,mode,round_number,git_sha,resuming:called.append(
            (mode,round_number,git_sha,resuming)))
    assert controller.recover() is True
    assert called==[("baseline",0,"b"*40,True)]


def test_terminal_training_is_never_auto_restarted(tmp_path,monkeypatch):
    c=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(tmp_path,stage="COMPLETE",mode="baseline",
        round_number=0,auto_resume=True,git_sha="b"*40,promoted=True)
    monkeypatch.setattr(c,"_launch",lambda **k:pytest.fail("should not relaunch"))
    assert c.recover() is False


def test_promotion_gate_crash_never_repeats_promotion_automatically(tmp_path,monkeypatch):
    c=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(tmp_path,stage="PROMOTION_GATE",mode="baseline",
        round_number=0,auto_resume=True,git_sha="b"*40)
    monkeypatch.setattr(c,"_launch",lambda **k:pytest.fail("unsafe promotion replay"))
    assert c.recover() is False
    assert training_cycle._run_state(tmp_path)["stage"]=="RECOVERY_BLOCKED"


def test_same_origin_pause_resume_api_blocks_cross_site(tmp_path):
    class FakeController:
        def pause(self):return {"pause_requested":True}
        def resume(self):return {"pause_requested":False}
    server=ThreadingHTTPServer(("127.0.0.1",0),
                              _make_handler(tmp_path,controller=FakeController()))
    th=threading.Thread(target=server.serve_forever,daemon=True)
    th.start()
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=4) as client:
            assert client.post(base+"/api/training/pause",json={}).status_code==403
            headers={"Origin":base,"X-KalshiEdge-Action":"train",
                     "Content-Type":"application/json"}
            p=client.post(base+"/api/training/pause",headers=headers,json={})
            assert p.status_code==202
            assert p.json()["pause_requested"]
            r=client.post(base+"/api/training/resume",headers=headers,json={})
            assert r.status_code==202
            assert not r.json()["pause_requested"]
    finally:
        server.shutdown()
        server.server_close()
        th.join(timeout=5)


def test_train_ui_has_pause_resume_and_shows_recovery_status():
    assert 'id="train-pause"' in PAGE
    assert 'id="train-resume"' in PAGE
    assert '"/api/training/pause"' in PAGE
    assert '"/api/training/resume"' in PAGE
    assert '"PAUSED"' in PAGE
    assert '"PAUSING"' in PAGE
    assert "resume_stage" in PAGE
