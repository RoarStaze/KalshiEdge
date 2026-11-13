"""Worker PID correctness and deterministic multi-core structural training."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
import sys

from kalshi_edge.bootstrap import structural, training, training_cycle


def test_worker_state_uses_actual_interpreter_not_windows_venv_redirector(tmp_path, monkeypatch):
    training_cycle._write_worker_state(
        tmp_path, stage="STARTING", mode="baseline", round_number=0,
        run_id="session", git_sha="a" * 40,
        worker_pid=13868, worker_identity="win:13868:old",
    )
    monkeypatch.setattr(training_cycle, "_active_interpreter_identity",
                        (13556, "win:13556:verified"))
    training_cycle._write_worker_state(
        tmp_path, stage="TRAINING", mode="baseline", round_number=0,
        fit_steps_completed=38, fit_steps_total=44,
        fit_step_detail="Selected candidate model and feature ablations",
    )
    saved = training_cycle._run_state(tmp_path)
    assert saved["worker_pid"] == 13556
    assert saved["worker_identity"] == "win:13556:verified"
    assert saved["run_id"] == "session"


def test_worker_main_records_interpreter_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(training_cycle.training_process_control, "identity",
                        lambda pid: f"pid-start:{pid}")
    observed = []
    monkeypatch.setattr(training_cycle, "run_training_worker",
                        lambda root, *, mode, round_number, git_sha:
                        observed.append(training_cycle._active_interpreter_identity) or 0)
    monkeypatch.setattr(sys, "argv", [
        "training_cycle", "--worker", "--root", str(tmp_path),
        "--mode", "baseline", "--round-number", "0",
        "--git-sha", "a" * 40,
    ])
    previous = getattr(training_cycle, "_active_interpreter_identity", None)
    try:
        assert training_cycle.main() == 0
        assert observed == [(os.getpid(), f"pid-start:{os.getpid()}")]
    finally:
        # Direct assignment avoids monkeypatch teardown restoring the fake PID.
        training_cycle._active_interpreter_identity = previous


def test_cpu_structural_worker_count_auto_and_override(monkeypatch):
    monkeypatch.delenv("KALSHI_TRAIN_STRUCTURAL_WORKERS", raising=False)
    assert training._structural_worker_count(platform="win32", cpu_count=12) == 3
    assert training._structural_worker_count(platform="linux", cpu_count=12) == 1
    monkeypatch.setenv("KALSHI_TRAIN_STRUCTURAL_WORKERS", "1")
    assert training._structural_worker_count(platform="win32", cpu_count=12) == 1
    monkeypatch.setenv("KALSHI_TRAIN_STRUCTURAL_WORKERS", "2")
    assert training._structural_worker_count(platform="linux", cpu_count=12) == 2
    monkeypatch.setenv("KALSHI_TRAIN_STRUCTURAL_WORKERS", "99")
    assert training._structural_worker_count(platform="win32", cpu_count=12) <= 4


def test_multi_process_structural_predictions_match_serial_exactly():
    """Same RNG seeds, output order, and probabilities for each causal row."""
    model = structural.StructuralModel(
        candidate="diffusion", seed=73115, simulations=16,
        drift_per_second=0.0, drift_cap_per_second=0.01,
        residuals=(-1.0, 0.0, 1.0),
    )
    states = tuple(
        structural.StructuralState(
            current_value=65000 + idx * 7,
            strike=65040,
            seconds_remaining=65 + idx % 15,
            volatility_per_second=0.001,
            recent_return_5s=0.0001,
        ) for idx in range(40)
    )
    baseline = [model.predict_proba(row) for row in states]
    with ProcessPoolExecutor(
        max_workers=2, mp_context=multiprocessing.get_context("spawn"),
    ) as executor:
        accelerated = structural.predict_probabilities(
            model, states, executor=executor,
        )
    assert accelerated == baseline


def test_structural_progress_reports_internal_work_without_fake_completed_operations():
    """Operation 39's inner work can update the detail, not inflate 38/44."""
    changes = []
    def record_detail(done, total, detail):
        changes.append((done, total, detail))
    reporter = training._structural_operation_progress(
        progress=record_detail, completed_operations=38, total_operations=44,
    )
    reporter("Fitting structural walk-forward fold 1/4")
    assert changes == [(38, 44, "Fitting structural walk-forward fold 1/4")]


def test_pause_targets_actual_interpreter_even_if_parent_is_venv_redirector(tmp_path, monkeypatch):
    class Launcher:
        pid = 13868
        def poll(self):
            return None
    controller = training_cycle.TrainingController(tmp_path, project_root=tmp_path)
    controller._process = Launcher()
    training_cycle._write_worker_state(
        tmp_path, stage="TRAINING", mode="baseline", round_number=0,
        run_id="session", git_sha="a" * 40,
        worker_pid=13556, worker_identity="win:13556:verified",
    )
    monkeypatch.setattr(
        training_cycle.training_process_control, "is_alive",
        lambda pid, token: pid == 13556 and token == "win:13556:verified",
    )
    frozen = []
    monkeypatch.setattr(
        training_cycle.training_process_control, "suspend",
        lambda pid: frozen.append(pid),
    )
    outcome = controller.pause()
    assert outcome["paused"] is True
    assert frozen == [13556]
    assert training_cycle._run_state(tmp_path)["suspended_in_memory"] is True
