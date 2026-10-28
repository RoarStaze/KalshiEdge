from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.bootstrap import artifact, evaluate, training
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.training_cycle import (
    CycleError, TrainingController, _completed_learning_rounds,
    _run_state, _state_path, inspect_training_readiness, run_training_worker,
)
from bootstrap.test_evaluate import _bundle


def _seal_cycle(root: Path, index: int, mode: str, promoted: bool = True) -> str:
    old = _bundle()
    bounds = old.boundaries.model_copy(update={
        "lockbox_start_ts_ns": 1000 + index * 200,
        "lockbox_end_ts_ns": 1050 + index * 200,
    })
    folder = "mistake_pool" if mode == "mistake" else "correct_pool"
    bundle = old.model_copy(update={
        "boundaries": bounds,
        "input_hashes": {
            "derived/features.parquet": "b" * 64,
            f"{folder}/2026-10-28/KXBTC15M-TRAIN-{index}.json": "c" * 64,
        },
        "bundle_sha256": None,
    })
    path = artifact.save_model_bundle(bundle, root / "models" / "experiments")
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / f"evaluation-{path.stem}.json").write_text(json.dumps({
        "report": {
            "bundle_sha256": path.stem,
            "lockbox_start_ts_ns": bounds.lockbox_start_ts_ns,
            "lockbox_end_ts_ns": bounds.lockbox_end_ts_ns,
        },
        "decision": {"promoted": promoted},
    }))
    return path.stem


def test_three_mistakes_then_one_correct_then_repeat(tmp_path: Path):
    assert inspect_training_readiness(tmp_path)["mode"] == "mistake"
    for idx, mode in enumerate(("mistake", "mistake", "mistake", "correct")):
        _seal_cycle(tmp_path, idx, mode, promoted=(idx % 2 == 0))
        result = inspect_training_readiness(tmp_path)
        assert result["completed_rounds"] == idx + 1
        assert result["mode"] == ("mistake" if idx < 2 else "correct" if idx == 2 else "mistake")
    assert inspect_training_readiness(tmp_path)["round_number"] == 5
    assert inspect_training_readiness(tmp_path)["cycle_position"] == 1
    assert len(_completed_learning_rounds(tmp_path)) == 4


def test_cycle_does_not_advance_before_evaluation_report(tmp_path: Path):
    bundle = _bundle().model_copy(update={
        "input_hashes": {"derived/features.parquet": "b" * 64, "mistake_pool/d/x.json": "c" * 64},
        "bundle_sha256": None,
    })
    path = artifact.save_model_bundle(bundle, tmp_path / "models" / "experiments")
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / "reports" / f"training-{path.stem}.json").write_text("{}")
    assert len(_completed_learning_rounds(tmp_path)) == 0
    assert inspect_training_readiness(tmp_path)["round_number"] == 1


def test_wrong_cycle_order_refused(tmp_path: Path):
    for index, mode in enumerate(("mistake", "correct")):
        _seal_cycle(tmp_path, index, mode)
    with pytest.raises(CycleError, match="sequence"):
        _completed_learning_rounds(tmp_path)


def test_training_readiness_stays_below_100_without_complete_verified_data(tmp_path: Path):
    status = inspect_training_readiness(tmp_path)
    assert not status["ready"]
    assert status["progress_percent"] < 100
    assert status["mode"] == "mistake"


def test_training_worker_persists_complete_state_even_if_model_not_promoted(tmp_path, monkeypatch):
    from kalshi_edge.bootstrap import training_cycle
    monkeypatch.setattr(training_cycle, "inspect_training_readiness", lambda root: {
        "ready": True, "mode": "mistake", "round_number": 1,
    })
    calls = []
    def fake_train(root, settings, *, git_sha, mistakes_only=False, correct_only=False):
        calls.append(("train", mistakes_only, correct_only))
        return SimpleNamespace(
            experiment_bundle_sha256="f" * 64,
            experiment_path=root / "models" / "experiments" / ("f" * 64 + ".json"),
        )
    def fake_evaluate(root, path):
        calls.append(("eval", path.name))
        return SimpleNamespace(
            decision=SimpleNamespace(promoted=False, reason_codes=("INCUMBENT_MODEL_NOT_BEATEN",)),
            report_path=root / "reports" / "evaluation-f.json",
        )
    monkeypatch.setattr(evaluate, "train_experiment", fake_train)
    monkeypatch.setattr(evaluate, "run_lockbox_evaluation", fake_evaluate)
    assert run_training_worker(tmp_path, mode="mistake", round_number=1, git_sha="a" * 40) == 0
    assert calls[0] == ("train", True, False)
    assert calls[1][0] == "eval"
    state = _run_state(tmp_path)
    assert state["stage"] == "COMPLETE"
    assert state["promoted"] is False


def test_training_worker_fails_closed_if_cycle_changed(tmp_path, monkeypatch):
    from kalshi_edge.bootstrap import training_cycle
    monkeypatch.setattr(training_cycle, "inspect_training_readiness", lambda root: {
        "ready": False, "mode": "mistake", "round_number": 1,
    })
    assert run_training_worker(tmp_path, mode="mistake", round_number=1, git_sha="a" * 40) == 2
    assert _run_state(tmp_path)["stage"] == "FAILED"


class FakeController:
    def __init__(self):
        self.started = 0

    def status(self):
        return {
            "mode": "mistake", "cycle_position": 1, "progress_percent": 100,
            "ready": True, "round_number": 1, "status": "TRAINING_READY",
            "running": False, "reason": "data verified",
        }

    def start(self):
        self.started += 1
        return {"started": True, "round_number": 1, "mode": "mistake"}


def test_training_api_requires_same_origin_and_click_header(tmp_path: Path):
    controller = FakeController()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(tmp_path, controller=controller))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5.0) as client:
            base = "http://127.0.0.1:" + str(server.server_port)
            status = client.get(base + "/api/training")
            assert status.status_code == 200
            assert status.json()["progress_percent"] == 100

            no_origin = client.post(base + "/api/training/start", json={})
            assert no_origin.status_code == 403

            wrong_origin = client.post(base + "/api/training/start", json={}, headers={
                "Origin": "http://evil.example", "X-KalshiEdge-Action": "train",
            })
            assert wrong_origin.status_code == 403

            correct = client.post(base + "/api/training/start", json={}, headers={
                "Origin": base, "X-KalshiEdge-Action": "train",
            })
            assert correct.status_code == 202
            assert correct.json()["started"] is True
            assert controller.started == 1
            assert client.post(base + "/api/training/start", json={"mode": "correct"}, headers={
                "Origin": base, "X-KalshiEdge-Action": "train",
            }).status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_training_controller_refuses_to_launch_without_readiness(tmp_path, monkeypatch):
    controller = TrainingController(tmp_path, project_root=tmp_path)
    monkeypatch.setattr(controller, "_readiness", lambda force=False: {
        "ready": False, "reason": "not enough newly evaluated markets",
    })
    with pytest.raises(CycleError, match="not enough"):
        controller.start()
    assert not (tmp_path / "training_runs" / "current.json").exists()


def test_refresher_training_partition_selects_only_correct_markets(monkeypatch, tmp_path):
    from kalshi_edge.bootstrap import correct_learning
    groups = [(f"KXBTC15M-G{i:04d}", 100000+i*100, 100050+i*100) for i in range(90)]
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "default.json").write_text("{}")
    monkeypatch.setattr(training, "_metadata_groups", lambda path: groups)
    selected = {g[0] for g in groups[20:40]}
    calls = []
    def fake_verified(root, *, allowed_tickers):
        calls.append(set(allowed_tickers))
        return tuple((
            SimpleNamespace(market_ticker=t, selected_label="yes"),
            tmp_path / "correct_pool" / "2026-10-28" / (t+".json"),
            "a" * 64,
        ) for t in sorted(selected & allowed_tickers))
    monkeypatch.setattr(correct_learning, "verified_correct_pool", fake_verified)
    plan, cases = training._correct_partition_plan(
        tmp_path, tmp_path / "features.parquet", previous_lockbox_end_ts_ns=101950,
    )
    assert set(plan.development_groups) == selected
    assert len(cases) == len(selected)
    assert set(plan.calibration_groups).isdisjoint(selected)
    assert set(plan.lockbox_groups).isdisjoint(selected)
    assert all(t not in calls[0] for t in plan.lockbox_groups)


def test_promotion_rejects_degradation_against_incumbent(tmp_path):
    from bootstrap.test_evaluate import _report, _metric
    report = _report(candidate=_metric(brier=.20, log_loss=.60, ece=.02),
                     prior=_metric(brier=.23, log_loss=.66, ece=.02))
    report = report.model_copy(update={"metrics": {
        **report.metrics,
        "incumbent_current": _metric(brier=.17, log_loss=.52, ece=.02),
    }})
    decision = evaluate.promotion_decision(report)
    assert not decision.promoted
    assert "INCUMBENT_MODEL_NOT_BEATEN" in decision.reason_codes


def test_promotion_can_pass_both_incumbent_and_kalshi(tmp_path):
    from bootstrap.test_evaluate import _report, _metric
    report = _report(candidate=_metric(brier=.14, log_loss=.41, ece=.02),
                     prior=_metric(brier=.23, log_loss=.66, ece=.02))
    report = report.model_copy(update={"metrics": {
        **report.metrics,
        "incumbent_current": _metric(brier=.18, log_loss=.52, ece=.02),
    }})
    assert evaluate.promotion_decision(report).promoted
