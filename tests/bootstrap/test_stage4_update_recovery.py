"""Checkpoint-resume regression for upgrades that change the training worker.

A finished or evaluated run must never be replayed under another Git SHA.
An unfinished Stage-4 fit can instead start a NEW lineage-tracked attempt using
the existing authenticated dataset, which the worker re-verifies before fitting.
"""
from __future__ import annotations

import subprocess

import pytest

from kalshi_edge.bootstrap import training_cycle, training_pause


def commit(repo, changes, message):
    if not (repo / ".git").is_dir():
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    for name, data in changes.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def setup(tmp_path, *, resume_stage="TRAINING", dataset_verified=True,
          mode="baseline", experiment_path=None, include_dataset=True):
    repo = tmp_path / "repository"
    old = commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 1\n",
        "src/kalshi_edge/bootstrap/features.py": "SCHEMA=1\n",
        "pyproject.toml": "project=1\n",
        "requirements.lock": "locked=1\n",
        "requirements.research.lock": "research=1\n",
    }, "original training code")
    root = tmp_path / "data"
    if include_dataset:
        feature = root / "derived" / "features.parquet"
        feature.parent.mkdir(parents=True)
        feature.write_bytes(b"sealed-test-placeholder")
        manifest = root / "manifests" / "dataset" / "features.parquet.manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}", encoding="utf-8")
        provenance = root / "derived" / "features.provenance.json"
        provenance.write_text("{}", encoding="utf-8")
    training_cycle._write_worker_state(
        root, stage="PAUSED", mode=mode, round_number=0, run_id="old-run",
        git_sha=old, resume_stage=resume_stage,
        dataset_verified=dataset_verified, dataset_markets_completed=515,
        dataset_markets_total=700, dataset_path=str(root / "derived" / "features.parquet"),
        fit_steps_completed=9, fit_steps_total=33,
        **({"experiment_path": str(experiment_path)} if experiment_path is not None else {}),
    )
    training_pause.set_pause(root, run_id="old-run", manual=True)
    controller = training_cycle.TrainingController(root, project_root=repo)
    return controller, repo, root, old


def recorded_launch(controller, monkeypatch):
    calls = []
    def launch(saved, *, resume):
        calls.append((dict(saved), resume))
        return {"resumed": True, "started": True, "run_id": saved["run_id"]}
    monkeypatch.setattr(controller, "_launch_existing", launch)
    return calls


def test_stage4_resume_after_training_code_change_forks_new_auditable_fit(tmp_path, monkeypatch):
    controller, repo, root, old = setup(tmp_path)
    calls = recorded_launch(controller, monkeypatch)
    new = commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n",
        "src/kalshi_edge/bootstrap/features.py": "SCHEMA=2\n",
    }, "new training code")
    result = controller.resume()
    assert result["resumed"] is True
    assert result["restarted_fit_after_update"] is True
    assert len(calls) == 1 and calls[0][1] is True
    record = calls[0][0]
    assert record["run_id"] != "old-run"
    assert record["git_sha"] == new
    assert record["forked_from_run_id"] == "old-run"
    assert record["forked_from_git_sha"] == old
    assert record["origin_git_sha"] == old
    assert record["resume_stage"] == "TRAINING"
    assert record["dataset_markets_completed"] == 515
    assert record["dataset_verified"] is True
    assert record["fit_steps_completed"] == 0
    assert record["fit_steps_total"] == 0
    assert training_pause.read_pause(root)["run_id"] == record["run_id"]
    assert training_pause.read_pause(root)["pause_requested"] is False
    assert (root / "training_runs" / "recovery" / "old-run.json").is_file()


def test_no_recovery_if_dataset_not_verified_or_missing(tmp_path, monkeypatch):
    for verified, present in ((False, True), (True, False)):
        suffix = f"{verified}-{present}"
        controller, repo, root, old = setup(tmp_path / suffix,
                                            dataset_verified=verified, include_dataset=present)
        calls = recorded_launch(controller, monkeypatch)
        commit(repo, {"src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n"},
               "new engine")
        with pytest.raises(training_cycle.CycleError, match="Git version changed"):
            controller.resume()
        assert calls == []
        assert training_cycle._run_state(root)["run_id"] == "old-run"
        assert training_pause.read_pause(root)["manual"] is True


def test_no_recovery_after_fit_is_saved_or_after_stage4(tmp_path, monkeypatch):
    for stage, experiment in (("TRAINING", "already-saved.json"), ("SCORING", None)):
        controller, repo, root, old = setup(tmp_path / stage, resume_stage=stage,
                                            experiment_path=experiment)
        calls = recorded_launch(controller, monkeypatch)
        commit(repo, {"src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n"},
               "new engine")
        with pytest.raises(training_cycle.CycleError, match="Git version changed"):
            controller.resume()
        assert calls == []
        assert training_cycle._run_state(root)["git_sha"] == old


def test_no_recovery_with_uncommitted_code_or_changed_dependency_lock(tmp_path, monkeypatch):
    for cause in ("dirty", "lock"):
        controller, repo, root, old = setup(tmp_path / cause)
        calls = recorded_launch(controller, monkeypatch)
        commit(repo, {"src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n"},
               "engine changed")
        if cause == "dirty":
            (repo / "src" / "kalshi_edge" / "bootstrap" / "features.py").write_text("SCHEMA=dirty\n")
        else:
            commit(repo, {"requirements.lock": "locked=2\n"}, "dependency changed")
        with pytest.raises(training_cycle.CycleError, match="Git version changed"):
            controller.resume()
        assert calls == []
        assert training_cycle._run_state(root)["run_id"] == "old-run"


def test_manual_paused_run_is_never_auto_restarted_due_to_git_update(tmp_path, monkeypatch):
    controller, repo, root, old = setup(tmp_path)
    calls = recorded_launch(controller, monkeypatch)
    commit(repo, {"src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n"},
           "new training")
    assert controller.recover() is False
    assert calls == []
    assert training_pause.read_pause(root)["manual"] is True


def test_pending_auto_recovery_forks_new_fit_only_if_authorized_and_safe(tmp_path, monkeypatch):
    controller, repo, root, old = setup(tmp_path)
    training_pause.set_pause(root, run_id="old-run", manual=False)
    calls = recorded_launch(controller, monkeypatch)
    new = commit(repo, {"src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n"},
                 "new training")
    assert controller.recover() is True
    assert len(calls) == 1 and calls[0][1] is True
    assert calls[0][0]["run_id"] != "old-run"
    assert calls[0][0]["git_sha"] == new
    assert calls[0][0]["dataset_markets_completed"] == 515


def test_stage4_recovery_uses_current_code_not_legacy_frozen_execution(tmp_path, monkeypatch):
    controller, repo, root, old = setup(tmp_path)
    new = commit(repo, {"src/kalshi_edge/bootstrap/training_cycle.py": "def model(): return 2\n"},
                 "new training")
    def fail_launch(*args, **kwargs):
        raise OSError("simulated subprocess launch error")
    monkeypatch.setattr(controller, "_launch_existing", fail_launch)
    with pytest.raises(OSError, match="simulated subprocess launch error"):
        controller.resume()
    # A failed subprocess spawn MUST NOT consume the user's original pause.
    assert training_cycle._run_state(root)["run_id"] == "old-run"
    assert training_cycle._run_state(root)["git_sha"] == old
    assert training_pause.read_pause(root)["manual"] is True
    assert (root / "training_runs" / "recovery" / "old-run.json").is_file()
