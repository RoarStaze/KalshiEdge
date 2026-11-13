"""Regression: a paused verified training checkpoint should survive safe Git updates.

Tests use real Git commits and persisted run/pause state; only the expensive
worker subprocess launch is replaced with a recording stub.
"""
from __future__ import annotations

import subprocess

import pytest

from kalshi_edge.bootstrap import training_cycle, training_pause


WORKER_CODE = (
    "def worker():\n    return 'unchanged'\n\n"
    "class TrainingController:\n    pass\n\n"
    "def main() -> int:\n    return 0\n"
)


def _commit(repo, files: dict[str, str], message: str) -> str:
    if not (repo / ".git").exists():
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Tests"], check=True)
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _controller(tmp_path, repo, original_sha, *, manual=True, stage="PAUSED"):
    data = tmp_path / "data"
    training_cycle._write_worker_state(
        data, stage=stage, mode="baseline", round_number=0,
        run_id="original-session", git_sha=original_sha,
        resume_stage="TRAINING", dataset_verified=True,
        dataset_markets_completed=516, dataset_markets_total=900,
        fit_steps_completed=3, fit_steps_total=24,
    )
    training_pause.set_pause(data, run_id="original-session", manual=manual)
    return training_cycle.TrainingController(data, project_root=repo), data


def _capture_launch(controller, monkeypatch):
    launched = []
    def start(saved, *, resume):
        launched.append((dict(saved), resume))
        return {"resumed": resume, "worker_pid": 1001}
    monkeypatch.setattr(controller, "_launch_existing", start)
    return launched


def test_manual_checkpoint_resumes_after_documentation_only_update(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
        "docs/README.md": "old\n",
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha)
    launched = _capture_launch(controller, monkeypatch)
    new_sha = _commit(repo, {"docs/README.md": "new\n"}, "docs only")
    assert new_sha != old_sha

    result = controller.resume()
    assert result["resumed"] is True
    assert launched[0][1] is True
    launched_run = launched[0][0]
    assert launched_run["run_id"] == "original-session"
    assert launched_run["git_sha"] == new_sha
    assert launched_run["origin_git_sha"] == old_sha
    assert launched_run["resume_stage"] == "TRAINING"
    assert launched_run["dataset_markets_completed"] == 516
    persisted = training_cycle._run_state(root)
    assert persisted["origin_git_sha"] == old_sha
    assert persisted["git_sha"] == new_sha
    assert persisted["dataset_markets_completed"] == 516
    assert training_pause.read_pause(root)["pause_requested"] is False


def test_manual_checkpoint_resumes_after_unrelated_autotrade_audit_update(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
        "src/kalshi_edge/trading/autotrade_profit_audit.py": "def audit(): return 1\n",
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha)
    launched = _capture_launch(controller, monkeypatch)
    new_sha = _commit(repo, {
        "src/kalshi_edge/trading/autotrade_profit_audit.py": "def audit(): return 2\n",
    }, "unrelated trading audit")
    assert controller.resume()["resumed"] is True
    assert launched[0][0]["git_sha"] == new_sha
    assert training_cycle._run_state(root)["origin_git_sha"] == old_sha


def test_checkpoint_preserves_earliest_origin_across_multiple_safe_updates(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
        "docs/README.md": "one\n",
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha)
    launched = _capture_launch(controller, monkeypatch)
    middle_sha = _commit(repo, {"docs/README.md": "two\n"}, "docs 2")
    assert controller.resume()["resumed"] is True
    assert launched[-1][0]["git_sha"] == middle_sha

    # A second manual pause/restart should retain the ORIGINAL provenance.
    training_cycle._write_worker_state(
        root, stage="PAUSED", mode="baseline", round_number=0,
        resume_stage="TRAINING",
    )
    training_pause.set_pause(root, run_id="original-session", manual=True)
    latest_sha = _commit(repo, {"docs/README.md": "three\n"}, "docs 3")
    assert controller.resume()["resumed"] is True
    assert launched[-1][0]["git_sha"] == latest_sha
    assert launched[-1][0]["origin_git_sha"] == old_sha


def test_checkpoint_rejects_modified_worker_code_without_changing_identity(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha)
    _capture_launch(controller, monkeypatch)
    _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE.replace("unchanged", "changed"),
    }, "worker change")

    with pytest.raises(training_cycle.CycleError, match="Git version changed"):
        controller.resume()
    assert training_cycle._run_state(root)["git_sha"] == old_sha
    assert training_pause.read_pause(root)["pause_requested"] is True


def test_checkpoint_rejects_runtime_dependency_change(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
        "src/kalshi_edge/bootstrap/features.py": "VERSION=1\n",
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha)
    _capture_launch(controller, monkeypatch)
    _commit(repo, {"src/kalshi_edge/bootstrap/features.py": "VERSION=2\n"}, "runtime changed")

    with pytest.raises(training_cycle.CycleError, match="Git version changed"):
        controller.resume()
    assert training_cycle._run_state(root)["git_sha"] == old_sha


def test_checkpoint_auto_recovery_after_compatible_update_preserves_saved_progress(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
        "docs/README.md": "old\n",
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha, manual=False, stage="BUILDING_DATASET")
    launched = _capture_launch(controller, monkeypatch)
    new_sha = _commit(repo, {"docs/README.md": "new\n"}, "docs only")
    assert controller.recover() is True
    assert launched[0][1] is True
    assert launched[0][0]["git_sha"] == new_sha
    assert launched[0][0]["dataset_markets_completed"] == 516
    assert training_cycle._run_state(root)["origin_git_sha"] == old_sha


def test_manual_pause_is_not_automatically_resumed_on_reopen_even_after_update(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": WORKER_CODE,
        "docs/README.md": "old\n",
    }, "initial")
    controller, root = _controller(tmp_path, repo, old_sha, manual=True)
    launches = _capture_launch(controller, monkeypatch)
    _commit(repo, {"docs/README.md": "new\n"}, "docs only")
    assert controller.recover() is False
    assert launches == []
    assert training_pause.read_pause(root)["manual"] is True


def test_updating_state_provenance_retention_does_not_invalidate_old_checkpoint(tmp_path, monkeypatch):
    """This fix itself must be compatible with a paused pre-fix checkpoint."""
    old_code = (
        'def _write_worker_state():\n'
        '    fields = ("worker_identity", "suspended_in_memory",)\n\n'
        + WORKER_CODE
    )
    new_code = old_code.replace(
        '"worker_identity", "suspended_in_memory",',
        '"worker_identity", "suspended_in_memory", "origin_git_sha",',
    )
    repo = tmp_path / "code"
    old_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": old_code,
    }, "before checkpoint-resume fix")
    controller, root = _controller(tmp_path, repo, old_sha)
    launched = _capture_launch(controller, monkeypatch)
    new_sha = _commit(repo, {
        "src/kalshi_edge/bootstrap/training_cycle.py": new_code,
    }, "add safe origin commit retention")

    assert controller.resume()["resumed"] is True
    assert launched[0][0]["git_sha"] == new_sha
    assert training_cycle._run_state(root)["origin_git_sha"] == old_sha
