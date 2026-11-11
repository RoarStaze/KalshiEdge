"""Instant Stage-4 pause: suspend the isolated worker, not trading.

The exact live in-memory model fit resumes when the same worker is thawed.
Closing the application still uses the existing durable checkpoint fallback.
"""
from __future__ import annotations

import pytest

from kalshi_edge.bootstrap import training_cycle, training_pause


class FakeWorker:
    def __init__(self):
        self.pid=45127
        self.exit_code=None
        self.wait_calls=[]
    def poll(self):
        return self.exit_code
    def wait(self,timeout=None):
        self.wait_calls.append(timeout)
        self.exit_code=0
        return 0


def setup_controller(root, stage="TRAINING"):
    controller=training_cycle.TrainingController(root,project_root=root)
    process=FakeWorker()
    controller._process=process
    training_cycle._write_worker_state(
        root,stage=stage,mode="baseline",round_number=0,
        run_id="live-run-1",git_sha="a"*40,worker_pid=process.pid,
        fit_steps_completed=3,fit_steps_total=25,
        fit_step_detail="Optimizing xgboost fold 2",
    )
    return controller,process


def test_pause_suspends_live_model_fit_immediately_and_resume_same_worker(tmp_path,monkeypatch):
    controller,process=setup_controller(tmp_path)
    called=[]
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
                        lambda pid:called.append(("suspend",pid)))
    monkeypatch.setattr(training_cycle.training_process_control,"resume",
                        lambda pid:called.append(("resume",pid)))
    monkeypatch.setattr(controller,"_git_sha",lambda:"a"*40)
    monkeypatch.setattr(controller,"_launch_existing",
                        lambda *a,**k:pytest.fail("must resume original fit, not spawn new process"))

    paused=controller.pause()
    assert paused["paused"] is True
    assert paused["pause_requested"] is True
    assert paused["immediate"] is True
    assert called==[("suspend",process.pid)]
    assert controller._process is process
    state=training_cycle._run_state(tmp_path)
    assert state["stage"]=="PAUSED"
    assert state["resume_stage"]=="TRAINING"
    assert state["fit_steps_completed"]==3
    assert state["fit_steps_total"]==25
    progress=controller.progress()
    assert progress["paused"] is True
    assert progress["can_resume"] is True
    assert progress["running"] is False
    assert training_pause.read_pause(tmp_path)["manual"] is True

    resumed=controller.resume()
    assert resumed["resumed"] is True
    assert resumed["in_memory"] is True
    assert called[-1]==("resume",process.pid)
    assert controller._process is process
    assert controller.progress()["running"] is True
    assert controller.progress()["paused"] is False
    assert training_cycle._run_state(tmp_path)["stage"]=="TRAINING"
    assert training_pause.read_pause(tmp_path)["pause_requested"] is False


def test_fitting_pause_falls_back_to_cooperative_when_suspend_unavailable(tmp_path,monkeypatch):
    controller,_=setup_controller(tmp_path)
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
                        lambda pid:(_ for _ in ()).throw(OSError("OS denies suspend")))
    response=controller.pause()
    assert response["paused"] is False
    assert response["pause_requested"] is True
    assert response["immediate"] is False
    assert controller.progress()["running"] is True
    assert controller.progress()["paused"] is False
    assert training_cycle._run_state(tmp_path)["stage"]=="TRAINING"


def test_dataset_pause_stays_at_verified_checkpoint_not_unsafe_process_suspension(tmp_path,monkeypatch):
    controller,_=setup_controller(tmp_path,stage="BUILDING_DATASET")
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
        lambda pid:pytest.fail("dataset checkpointing must not be forcibly suspended"))
    response=controller.pause()
    assert response["immediate"] is False
    assert response["pause_requested"] is True
    assert controller.progress()["running"] is True


def test_stop_thaws_suspended_worker_before_shutdown(tmp_path,monkeypatch):
    controller,process=setup_controller(tmp_path)
    called=[]
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
                        lambda pid:called.append(("suspend",pid)))
    monkeypatch.setattr(training_cycle.training_process_control,"resume",
                        lambda pid:called.append(("resume",pid)))
    assert controller.pause()["paused"] is True
    controller.stop()
    assert called==[("suspend",process.pid),("resume",process.pid)]
    assert process.wait_calls==[2]
    assert controller._process is None
    assert controller._suspended is False
    assert training_pause.read_pause(tmp_path)["manual"] is True


def test_suspended_progress_is_never_incorrectly_reported_as_running(tmp_path,monkeypatch):
    controller,process=setup_controller(tmp_path)
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",lambda pid:None)
    monkeypatch.setattr(training_cycle.training_process_control,"resume",lambda pid:None)
    controller.pause()
    monkeypatch.setattr(controller,"_readiness",lambda **kw:{"ready":True})
    monkeypatch.setattr(training_cycle,"inspect_baseline_training_readiness",
                        lambda root:{"ready":True})
    # Status reporting must not treat a suspended worker as actively fitting.
    info=controller.progress()
    assert info["running"] is False
    assert info["paused"] is True
    assert info["run"]["stage"]=="PAUSED"


def test_training_ui_recognizes_immediately_paused_response():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert 'result.paused===true' in PAGE
    assert 'trainingStatus?.paused' in PAGE or 'await refreshTraining()' in PAGE
