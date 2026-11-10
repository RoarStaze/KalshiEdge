"""Pause, persisted dataset checkpoints, and app-restart recovery tests.

All tests are offline: no exchange credentials, trades, or model promotions.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import training_cycle, training_pause


def test_pause_request_is_durable_and_is_scoped_to_the_current_run(tmp_path):
    training_pause.set_pause(tmp_path, run_id="run-A", manual=True)
    with pytest.raises(training_pause.TrainingPaused):
        training_pause.check_pause(tmp_path, "run-A")
    # A stale request from a previous run must never pause a different run.
    training_pause.check_pause(tmp_path, "run-B")
    assert training_pause.read_pause(tmp_path)["manual"] is True
    training_pause.clear_pause(tmp_path, run_id="run-A")
    training_pause.check_pause(tmp_path, "run-A")


def test_dataset_callback_pauses_on_next_checkpoint_without_restarting_market(tmp_path):
    observed = []
    def fake_write(root, **kwargs):
        observed.append(kwargs)
    from kalshi_edge.bootstrap import training_pause
    training_pause.set_pause(tmp_path, run_id="dataset-run", manual=True)
    reporter = training_cycle._market_progress_reporter(
        tmp_path, mode="baseline", round_number=0, run_id="dataset-run",
    )
    with pytest.raises(training_pause.TrainingPaused):
        reporter(132, 1000, "KXBTC15M-MARKET-132", "markets")
    # A different run ID is not affected by the request.
    reporter2 = training_cycle._market_progress_reporter(
        tmp_path, mode="baseline", round_number=0, run_id="later-run",
    )
    training_pause.check_pause(tmp_path, "later-run")


def test_paused_worker_state_retains_stage_count_and_original_version(tmp_path):
    training_cycle._write_worker_state(
        tmp_path, stage="BUILDING_DATASET", mode="baseline",
        round_number=0, run_id="run-1", git_sha="a"*40,
        dataset_markets_completed=21234, dataset_markets_total=29865,
    )
    training_cycle._save_paused_state(tmp_path, mode="baseline",
                                       round_number=0, reason="manual")
    state=training_cycle._run_state(tmp_path)
    assert state["stage"]=="PAUSED"
    assert state["resume_stage"]=="BUILDING_DATASET"
    assert state["dataset_markets_completed"]==21234
    assert state["dataset_markets_total"]==29865
    assert state["run_id"]=="run-1"
    assert state["git_sha"]=="a"*40
    assert state["progress_percent"]>0


def test_manual_pause_survives_reopening_without_auto_resume(tmp_path,monkeypatch):
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(
        tmp_path,stage="PAUSED",mode="baseline",round_number=0,
        run_id="manual-1",git_sha="b"*40,resume_stage="BUILDING_DATASET",
    )
    training_pause.set_pause(tmp_path,run_id="manual-1",manual=True)
    calls=[]
    monkeypatch.setattr(controller,"_launch_existing",lambda *a,**k:calls.append(k))
    assert controller.recover() is False
    assert calls==[]
    assert controller.progress()["paused"] is True
    assert controller.progress()["can_resume"] is True


def test_active_run_auto_resumes_after_app_restart(tmp_path,monkeypatch):
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(
        tmp_path,stage="BUILDING_DATASET",mode="baseline",round_number=0,
        run_id="auto-1",git_sha="b"*40,
        dataset_markets_completed=21900,dataset_markets_total=29865,
    )
    monkeypatch.setattr(controller,"_git_sha",lambda:"b"*40)
    launched=[]
    monkeypatch.setattr(controller,"_launch_existing",lambda state,**kwargs:
                        launched.append((state,kwargs)))
    assert controller.recover() is True
    assert len(launched)==1
    assert launched[0][0]["run_id"]=="auto-1"
    assert launched[0][0]["dataset_markets_completed"]==21900
    assert launched[0][1]["resume"] is True


def test_git_version_change_blocks_unsafe_automatic_resume(tmp_path,monkeypatch):
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(
        tmp_path,stage="BUILDING_DATASET",mode="baseline",round_number=0,
        run_id="auto-2",git_sha="a"*40,
    )
    monkeypatch.setattr(controller,"_git_sha",lambda:"b"*40)
    monkeypatch.setattr(controller,"_launch_existing",lambda *a,**k:
                        pytest.fail("must not start incompatible experiment"))
    assert controller.recover() is False
    assert "Git" in controller.progress()["run"]["error"]


def test_explicit_pause_resume_preserve_same_run_identity(tmp_path,monkeypatch):
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    training_cycle._write_worker_state(
        tmp_path,stage="PAUSED",mode="forward_480",round_number=0,
        run_id="run-F",git_sha="a"*40,resume_stage="SCORING",
    )
    monkeypatch.setattr(controller,"_git_sha",lambda:"a"*40)
    training_pause.set_pause(tmp_path,run_id="run-F",manual=True)
    launches=[]
    monkeypatch.setattr(controller,"_launch_existing",lambda state,**kwargs:
                        launches.append((state,kwargs)))
    response=controller.resume()
    assert response["resumed"] is True
    assert launches[0][0]["run_id"]=="run-F"
    assert training_pause.read_pause(tmp_path)["pause_requested"] is False


def test_resuming_completed_fit_uses_existing_authenticated_experiment(tmp_path,monkeypatch):
    # If a successful fit was saved, evaluation must not fit the model again.
    from kalshi_edge.bootstrap import evaluate, artifact
    saved=tmp_path/"models"/"experiments"/("a"*64+".json")
    saved.parent.mkdir(parents=True)
    saved.write_text("{}")
    assert training_cycle._resume_experiment_path(
        tmp_path,{"mode":"baseline","experiment_path":str(saved),
                  "resume_stage":"EVALUATING"},
    ) == saved
    assert training_cycle._resume_experiment_path(
        tmp_path,{"mode":"baseline","experiment_path":str(saved),
                  "resume_stage":"BUILDING_DATASET"},
    ) is None
    (tmp_path/"reports").mkdir()
    (tmp_path/"reports"/("evaluation-"+"a"*64+".json")).write_text("{}")
    with pytest.raises(training_cycle.CycleError):
        training_cycle._resume_experiment_path(
            tmp_path,{"mode":"baseline","experiment_path":str(saved),
                      "resume_stage":"SCORING"},
        )


def test_pause_api_requires_train_click_authorization(tmp_path):
    import httpx
    import threading
    from http.server import ThreadingHTTPServer
    from kalshi_edge.bootstrap.final_history import _make_handler
    class FakeController:
        def pause(self): return {"pause_requested":True}
        def resume(self): return {"resumed":True}
    server=ThreadingHTTPServer(("127.0.0.1",0),
          _make_handler(tmp_path,controller=FakeController()))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5) as client:
            base=f"http://127.0.0.1:{server.server_port}"
            for action in ("pause","resume"):
                url=base+"/api/training/"+action
                assert client.post(url,json={}).status_code==403
                assert client.post(url,json={"override":1},headers={
                    "Origin":base,"X-KalshiEdge-Action":"train",
                }).status_code==400
                response=client.post(url,json={},headers={
                    "Origin":base,"X-KalshiEdge-Action":"train",
                })
                assert response.status_code==202
                assert response.json()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_training_page_has_real_pause_resume_controls_and_statuses():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert 'id="train-pause"' in PAGE
    assert 'id="train-resume"' in PAGE
    assert '"/api/training/pause"' in PAGE
    assert '"/api/training/resume"' in PAGE
    assert '"PAUSE_REQUESTED"' in PAGE
    assert '"PAUSED"' in PAGE
    assert "run.resume_stage" in PAGE


def test_training_worker_restarts_checkpointed_dataset_without_resetting_counter(tmp_path,monkeypatch):
    values=[]
    monkeypatch.setattr(training_cycle,"_write_worker_state",
                        lambda root,**kw:values.append(kw))
    reporter=training_cycle._market_progress_reporter(
        tmp_path,mode="baseline",round_number=0,run_id="run-1",
        initial_completed=200,clock=lambda:2.0,
    )
    for i in range(1,201):
        reporter(i,500,f"market-{i}","markets")
    assert values==[]
    reporter(203,500,"market-203","markets")
    assert values[-1]["dataset_markets_completed"]==203


def test_forward_resume_reuses_same_sealed_dataset_not_new_settlements(tmp_path, monkeypatch):
    from kalshi_edge.bootstrap import forward_learning_480
    folder=tmp_path/"training_runs"/"datasets"
    folder.mkdir(parents=True)
    path=folder/("forward-480-"+"a"*64+".json")
    path.write_text("{}")
    training_cycle._write_worker_state(
        tmp_path,stage="PAUSED",mode="forward_480",round_number=0,
        run_id="frozen1",git_sha="a"*40,
        resume_stage="SCORING",dataset_path=str(path),dataset_verified=True,
    )
    training_pause.reset_pause(tmp_path, run_id="frozen1")
    monkeypatch.setattr(
        forward_learning_480,"create_verified_forward_dataset",
        lambda root: pytest.fail("must reuse original frozen 480s snapshot"),
    )
    monkeypatch.setattr(
        forward_learning_480,"load_verified_forward_dataset",
        lambda actual: (["valid-saved-row"], {"prior":"frozen"}),
    )
    monkeypatch.setattr(
        forward_learning_480,"assess_forward_training_readiness",
        lambda rows: {"ready":True,"eligible_480_markets":1},
    )
    seen=[]
    def fake_evaluation(root, *, progress, dataset_path):
        seen.append(dataset_path)
        return {"status":"NO_VALIDATED_JOINT_GAIN"}
    monkeypatch.setattr(
        forward_learning_480,"evaluate_verified_forward_candidate",fake_evaluation,
    )
    assert training_cycle.run_forward_training_worker(tmp_path)==0
    assert seen==[path]
    assert training_cycle._run_state(tmp_path)["stage"]=="COMPLETE"
