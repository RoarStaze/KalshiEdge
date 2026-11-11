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


@pytest.fixture(autouse=True)
def fake_os_identity(monkeypatch):
    monkeypatch.setattr(training_cycle.training_process_control,"identity",
                        lambda pid:f"fake-start:{pid}")
    monkeypatch.setattr(training_cycle.training_process_control,"is_alive",
                        lambda pid,token:token==f"fake-start:{pid}")


def setup_controller(root, stage="TRAINING"):
    controller=training_cycle.TrainingController(root,project_root=root)
    process=FakeWorker()
    controller._process=process
    training_cycle._write_worker_state(
        root,stage=stage,mode="baseline",round_number=0,
        run_id="live-run-1",git_sha="a"*40,worker_pid=process.pid,
        worker_identity=f"fake-start:{process.pid}",
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


def test_manual_pause_survives_app_close_then_resumes_same_in_memory_fit(tmp_path,monkeypatch):
    controller,process=setup_controller(tmp_path)
    called=[]
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
                        lambda pid:called.append(("suspend",pid)))
    monkeypatch.setattr(training_cycle.training_process_control,"resume",
                        lambda pid:called.append(("resume",pid)))
    assert controller.pause()["paused"] is True
    controller.stop()
    assert called==[("suspend",process.pid)]
    assert process.wait_calls==[]
    assert controller._process is None
    assert controller._suspended is False
    assert training_pause.read_pause(tmp_path)["manual"] is True

    # New dashboard adopts the frozen worker by PID + creation identity.
    reopened=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    monkeypatch.setattr(reopened,"_git_sha",lambda:"a"*40)
    monkeypatch.setattr(reopened,"_launch_existing",
                        lambda *a,**k:pytest.fail("must resume original fit"))
    assert reopened.recover() is False  # manual pause remains manual
    assert reopened.progress()["paused"] is True
    resumed=reopened.resume()
    assert resumed["in_memory"] is True
    assert called==[("suspend",process.pid),("resume",process.pid)]
    assert reopened.progress()["running"] is True


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


def test_active_fit_auto_suspends_on_close_and_auto_resumes_on_reopen(tmp_path,monkeypatch):
    controller,worker=setup_controller(tmp_path)
    called=[]
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
                        lambda pid:called.append(("suspend",pid)))
    monkeypatch.setattr(training_cycle.training_process_control,"resume",
                        lambda pid:called.append(("resume",pid)))
    controller.stop()
    assert called==[("suspend",worker.pid)]
    state=training_cycle._run_state(tmp_path)
    assert state["stage"]=="PAUSED"
    assert state["suspended_in_memory"] is True
    assert state["resume_stage"]=="TRAINING"
    assert training_pause.read_pause(tmp_path)["manual"] is False
    reopened=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    monkeypatch.setattr(reopened,"_git_sha",lambda:"a"*40)
    monkeypatch.setattr(reopened,"_launch_existing",
                        lambda *a,**k:pytest.fail("restarted instead of thawing worker"))
    assert reopened.recover() is True
    assert called[-1]==("resume",worker.pid)
    assert reopened.progress()["running"] is True
    assert reopened.progress()["run"]["stage"]=="TRAINING"


def test_stale_pid_is_not_suspended_or_adopted_on_reopen(tmp_path,monkeypatch):
    controller,worker=setup_controller(tmp_path)
    monkeypatch.setattr(training_cycle.training_process_control,"is_alive",
                        lambda pid,token:False)
    monkeypatch.setattr(training_cycle.training_process_control,"suspend",
                        lambda pid:pytest.fail("must not suspend stale PID"))
    response=controller.pause()
    assert response["immediate"] is False
    assert response["pause_requested"] is True
    assert training_cycle._run_state(tmp_path)["stage"]=="TRAINING"


def test_process_identity_rejects_invalid_and_recycled_pid(monkeypatch):
    from kalshi_edge.bootstrap import training_process_control as control
    # The fixture stubs OS identity for the fake worker; validate PID
    # rejection through the unchanged low-level PID gate.
    with pytest.raises(control.ProcessControlError):
        control._pid(0)
    with pytest.raises(control.ProcessControlError):
        control.suspend(0)
    assert control.is_alive(2147483001,"recycled") is False


def _commit_training_source(root, *, training_source="original", readme="initial"):
    """Create a real local Git tree to test a version-change safety decision."""
    import subprocess

    repo = root / "versioned-source"
    if not (repo / ".git").is_dir():
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "CI"], check=True)
    src = repo / "src" / "kalshi_edge" / "bootstrap"
    src.mkdir(parents=True, exist_ok=True)
    (src / "training_cycle.py").write_text(training_source, encoding="utf-8")
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "README.md").write_text(readme, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", readme], check=True)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return repo, sha


def test_resume_live_frozen_fit_when_only_docs_changed(tmp_path, monkeypatch):
    """A documentation-only commit must not discard a still-frozen fit."""
    repo, old_sha = _commit_training_source(tmp_path)
    controller, process = setup_controller(tmp_path)
    controller.project_root = repo
    training_cycle._write_worker_state(
        tmp_path, stage="TRAINING", mode="baseline", round_number=0,
        git_sha=old_sha,
    )
    monkeypatch.setattr(training_cycle.training_process_control, "suspend", lambda pid: None)
    thawed = []
    monkeypatch.setattr(training_cycle.training_process_control, "resume",
                        lambda pid: thawed.append(pid))
    assert controller.pause()["paused"] is True

    repo, new_sha = _commit_training_source(tmp_path, readme="documentation changed")
    assert old_sha != new_sha
    result = controller.resume()

    assert result["resumed"] is True
    assert result["in_memory"] is True
    assert thawed == [process.pid]
    assert controller.progress()["running"] is True
    assert training_cycle._run_state(tmp_path)["git_sha"] == old_sha
    assert training_pause.read_pause(tmp_path)["pause_requested"] is False


def test_resume_live_frozen_fit_rejects_changed_training_code(tmp_path, monkeypatch):
    """A new training implementation cannot be mixed into a frozen old run."""
    repo, old_sha = _commit_training_source(tmp_path)
    controller, process = setup_controller(tmp_path)
    controller.project_root = repo
    training_cycle._write_worker_state(
        tmp_path, stage="TRAINING", mode="baseline", round_number=0,
        git_sha=old_sha,
    )
    monkeypatch.setattr(training_cycle.training_process_control, "suspend", lambda pid: None)
    monkeypatch.setattr(training_cycle.training_process_control, "resume",
                        lambda pid: pytest.fail("changed code must not be thawed"))
    assert controller.pause()["paused"] is True
    _commit_training_source(tmp_path, training_source="modified-runtime", readme="runtime update")
    with pytest.raises(training_cycle.CycleError, match="Git version changed"):
        controller.resume()
    assert controller.progress()["paused"] is True
    assert training_pause.read_pause(tmp_path)["pause_requested"] is True


def test_resume_frozen_fit_after_controller_only_update(tmp_path, monkeypatch):
    """A new dashboard controller does not replace the old suspended worker."""
    worker_and_main = (
        "def worker():\n    return 1\n\n"
        "class TrainingController:\n    pass\n\n"
        "def main() -> int:\n    return 0\n"
    )
    repo, old_sha = _commit_training_source(
        tmp_path, training_source=worker_and_main,
    )
    controller, process = setup_controller(tmp_path)
    controller.project_root = repo
    training_cycle._write_worker_state(
        tmp_path, stage="TRAINING", mode="baseline", round_number=0,
        git_sha=old_sha,
    )
    monkeypatch.setattr(training_cycle.training_process_control, "suspend", lambda pid: None)
    resumed = []
    monkeypatch.setattr(training_cycle.training_process_control, "resume",
                        lambda pid: resumed.append(pid))
    assert controller.pause()["paused"] is True

    controller_only = worker_and_main.replace(
        "class TrainingController:\n    pass",
        "class TrainingController:\n    def resume(self):\n        return True",
    )
    _commit_training_source(
        tmp_path, training_source=controller_only, readme="controller-only fix",
    )
    assert controller.resume()["in_memory"] is True
    assert resumed == [process.pid]
    assert training_cycle._run_state(tmp_path)["git_sha"] == old_sha


def test_paused_disk_checkpoint_still_rejects_git_version_change(tmp_path, monkeypatch):
    """Never restart an old on-disk experiment under newly changed code."""
    controller = training_cycle.TrainingController(tmp_path, project_root=tmp_path)
    training_cycle._write_worker_state(
        tmp_path, stage="PAUSED", mode="baseline", round_number=0,
        run_id="disk-run", git_sha="a" * 40,
        resume_stage="BUILDING_DATASET", suspended_in_memory=False,
    )
    training_pause.set_pause(tmp_path, run_id="disk-run", manual=True)
    monkeypatch.setattr(controller, "_git_sha", lambda: "b" * 40)
    monkeypatch.setattr(controller, "_launch_existing",
                        lambda *args, **kwargs: pytest.fail("must not launch changed code"))

    with pytest.raises(training_cycle.CycleError, match="Git version changed"):
        controller.resume()
    assert training_pause.read_pause(tmp_path)["pause_requested"] is True


def test_resume_failure_remains_visible_after_refresh():
    """A rejected API resume must not appear to do nothing after the status GET."""
    import shutil
    import subprocess
    from kalshi_edge.bootstrap.dashboard_ui import PAGE

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for browser-control regression test")
    start = PAGE.index("async function trainingControl(action){")
    end = PAGE.index('\nel("train-pause").addEventListener', start)
    function_source = PAGE[start:end]
    script = (
        "let trainingActionError='';let feedback='';let refreshes=0;\n"
        "const button={disabled:false};\n"
        "function el(id){if(id!=='train-resume')throw Error('bad button: '+id);return button;}\n"
        "function text(id,value){if(id==='train-feedback')feedback=value;}\n"
        "async function fetch(){return {ok:false,status:409,"
        "json:async()=>({error:'Git version changed since training paused'})};}\n"
        "async function refreshTraining(){refreshes++;"
        "text('train-feedback','Paused at saved checkpoint; click Resume.');}\n"
        + function_source + "\n"
        + "(async()=>{await trainingControl('resume');"
        "if(refreshes!==1)throw Error('status refresh skipped');"
        "if(!feedback.includes('Git version changed'))"
        "throw Error('API failure was erased: '+feedback);"
        "})().catch(e=>{console.error(e);process.exitCode=1;});\n"
    )
    result = subprocess.run(
        [node, "-"], input=script, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
