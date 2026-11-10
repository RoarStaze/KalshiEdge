"""Fast, lightweight training progress without extra expensive training scans."""
from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import threading
from urllib.request import urlopen

from kalshi_edge.bootstrap import training_cycle
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.final_history import _make_handler
from kalshi_edge.bootstrap.site_pages import render_site_page


def test_market_counter_reports_every_three_or_quarter_second_without_write_amplification(
    tmp_path, monkeypatch,
):
    writes = []
    clock = [0.0]
    monkeypatch.setattr(training_cycle, "_write_worker_state",
        lambda root, **kwargs: writes.append((clock[0], kwargs.copy())))
    update = training_cycle._market_progress_reporter(
        tmp_path, mode="baseline", round_number=0, clock=lambda: clock[0],
        min_markets=3, min_interval_seconds=0.25,
    )
    for completed in range(1, 30):
        update(completed, 100, "KXBTC15M-TEST", "markets")
        clock[0] += 0.06
    assert writes[0][1]["dataset_markets_completed"] == 1
    assert len(writes) >= 4
    assert all(
        now - earlier >= 0.24999
        for (earlier, _), (now, _) in zip(writes, writes[1:])
    )
    assert all(
        right["dataset_markets_completed"] - left["dataset_markets_completed"] >= 3
        for (_, left), (_, right) in zip(writes, writes[1:])
    )
    update(100, 100, "validating leakage and writing artifacts", "finalizing")
    assert writes[-1][1]["dataset_markets_completed"] == 100
    assert "finaliz" in writes[-1][1]["dataset_action"].lower()


def test_three_markets_shows_immediately_when_disk_budget_allows(tmp_path, monkeypatch):
    values = []
    clock = [0.]
    monkeypatch.setattr(training_cycle, "_write_worker_state",
        lambda root, **kw: values.append(kw["dataset_markets_completed"]))
    callback = training_cycle._market_progress_reporter(
        tmp_path, mode="baseline", round_number=0, clock=lambda: clock[0],
    )
    callback(1, 29865, "a", "markets")
    clock[0] = 1.
    callback(2, 29865, "b", "markets")
    callback(3, 29865, "c", "markets")
    callback(4, 29865, "d", "markets")
    assert values == [1, 4]


def test_lightweight_progress_does_not_compute_training_readiness(tmp_path, monkeypatch):
    controller = training_cycle.TrainingController(tmp_path, project_root=tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Expensive training readiness must never run during fast polling")
    monkeypatch.setattr(controller, "_readiness", forbidden)
    monkeypatch.setattr(training_cycle, "inspect_baseline_training_readiness", forbidden)
    training_cycle._write_worker_state(tmp_path, stage="BUILDING_DATASET",
                                      mode="baseline", round_number=0,
                                      dataset_markets_completed=3,
                                      dataset_markets_total=29865)
    response = controller.progress()
    assert response["running"] is False
    assert response["run"]["stage"] == "INTERRUPTED"
    assert response["run"]["dataset_markets_completed"] == 3


def test_lightweight_training_endpoint_only_returns_worker_state(tmp_path):
    class FakeController:
        def status(self):
            raise AssertionError("No expensive status() on lightweight endpoint")
        def progress(self):
            return {"running": True,
                    "run": {"stage": "BUILDING_DATASET",
                            "dataset_markets_completed": 321,
                            "dataset_markets_total": 29865,
                            "progress_percent": 8}}
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 _make_handler(tmp_path, controller=FakeController()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/training/progress",
                     timeout=3) as response:
            data = json.load(response)
        assert data["run"]["dataset_markets_completed"] == 321
        assert data["running"] is True
        assert "baseline_retrain" not in data
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_train_page_removes_legacy_four_round_readiness():
    assert "Legacy four-round learning cycle" not in PAGE
    assert 'id="training-legacy-details"' not in PAGE
    assert 'id="train-step-1"' not in PAGE
    assert 'id="training-cases"' not in PAGE
    assert 'id="training-bar-fill"' not in PAGE
    assert 'id="training-worker-progress"' in PAGE
    assert 'id="dataset-build-detail"' in PAGE
    assert 'id="dataset-build-bar"' in PAGE
    assert 'id="dataset-build-bar-fill"' in PAGE
    assert 'el("dataset-build-bar-fill").style.width' in PAGE


def test_train_route_polls_light_state_faster_than_full_readiness():
    # Keep the full 5s readiness/status check; a separate 750ms progress read
    # avoids expensive refits/re-scans and only runs while the Train page is active.
    from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
    p = render_site_page(
        integrate_api_credentials(
            integrate_manual_trade(integrate_round_price_hero(PAGE))
        ),
        "train",
    )
    assert 'refreshTrainingProgress' in p
    assert 'setInterval(refreshTrainingProgress,750)' in p
    assert 'setInterval(refreshTraining,5000)' in p
    assert '"/api/training/progress"' in p
    assert 'if(progressPollPending||document.hidden' in p
    assert 'document.hidden' in p
    assert '"current_model"' not in p[p.index('async function refreshTrainingProgress()'):p.index('el("train-now").addEventListener')]
