from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kalshi_edge import launcher


class FakeProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.terminate()


def test_dashboard_health_checks_application_identity(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return b'{"app":"kalshiedge","service":"unified-dashboard","ready":true}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    assert launcher._dashboard_healthy(8899)


def test_dashboard_health_rejects_unrelated_server(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return b'{"app":"not-kalshiedge","service":"unified-dashboard"}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    assert not launcher._dashboard_healthy(8899)


def test_one_terminal_launcher_starts_both_services_and_stops_children(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "BootstrapSettings", lambda: SimpleNamespace(bootstrap_dir=tmp_path / "bootstrap"))
    monkeypatch.setattr(
        launcher, "load_default_bundle",
        lambda root: SimpleNamespace(model_version="bootstrap-hybrid-v5", bundle_sha256="a" * 64),
    )
    monkeypatch.setattr(launcher, "_tcp_busy", lambda port: False)

    spawned = []
    fake_history = FakeProcess(100)
    fake_predictor = FakeProcess(101)

    def spawn(args, root, log):
        spawned.append(args)
        return fake_history if len(spawned) == 1 else fake_predictor

    monkeypatch.setattr(launcher, "_spawn_service", spawn)
    monkeypatch.setattr(launcher, "_dashboard_healthy", lambda port: len(spawned) >= 1)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)

    def short_sleep(seconds):
        if seconds == 1.0:
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher.time, "sleep", short_sleep)
    assert launcher.start() == 0
    assert spawned == [["final-history", "--port", "8899"], ["predict-live"]]
    assert opened == ["http://127.0.0.1:8899/"]
    assert fake_history.stopped
    assert fake_predictor.stopped


def test_port_conflict_refuses_start_without_launching_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "BootstrapSettings", lambda: SimpleNamespace(bootstrap_dir=tmp_path / "bootstrap"))
    monkeypatch.setattr(
        launcher, "load_default_bundle",
        lambda root: SimpleNamespace(model_version="bootstrap-hybrid-v5", bundle_sha256="a" * 64),
    )
    monkeypatch.setattr(launcher, "_dashboard_healthy", lambda port: False)
    monkeypatch.setattr(launcher, "_tcp_busy", lambda port: True)
    monkeypatch.setattr(launcher, "_spawn_service", Mock(side_effect=AssertionError("must not spawn")))
    assert launcher.start(open_browser=False) == 2


def test_launcher_reuses_existing_unified_history_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "BootstrapSettings", lambda: SimpleNamespace(bootstrap_dir=tmp_path / "bootstrap"))
    monkeypatch.setattr(
        launcher, "load_default_bundle",
        lambda root: SimpleNamespace(model_version="bootstrap-hybrid-v5", bundle_sha256="a" * 64),
    )
    monkeypatch.setattr(launcher, "_dashboard_healthy", lambda port: True)
    monkeypatch.setattr(launcher, "_tcp_busy", Mock(side_effect=AssertionError("should reuse healthy server")))
    spawned = []
    fake_predictor = FakeProcess(101)
    monkeypatch.setattr(launcher, "_spawn_service", lambda args, root, log: spawned.append(args) or fake_predictor)

    def short_sleep(seconds):
        if seconds == 1.0:
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher.time, "sleep", short_sleep)
    assert launcher.start(open_browser=False) == 0
    assert spawned == [["predict-live"]]
    assert fake_predictor.stopped


def test_same_launch_lock_cannot_be_reacquired(tmp_path):
    lock = tmp_path / "lock"
    with launcher.SingleStarter(lock):
        with pytest.raises(RuntimeError, match="already running"):
            with launcher.SingleStarter(lock):
                pass
    with launcher.SingleStarter(lock):
        pass


def test_launcher_parser_supports_only_explicit_start(monkeypatch):
    actions = []
    monkeypatch.setattr(launcher, "start", lambda **kwargs: actions.append(kwargs) or 0)
    assert launcher.main(["start", "--no-browser", "--port", "8900"]) == 0
    assert actions == [{"port": 8900, "open_browser": False}]
    with pytest.raises(SystemExit):
        launcher.main(["invalid"])


def test_launcher_requests_durable_checkpoint_before_own_history_tree_shutdown(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import training_cycle, training_pause
    training_cycle._write_worker_state(
        tmp_path,stage="BUILDING_DATASET",mode="baseline",round_number=0,
        run_id="shutdown-run",git_sha="a"*40,
        dataset_markets_completed=250,dataset_markets_total=300,
    )
    training_pause.reset_pause(tmp_path,run_id="shutdown-run")
    monkeypatch.setattr(launcher.time,"sleep",lambda *args:None)
    launcher._request_training_shutdown(tmp_path,timeout_seconds=0)
    intent=training_pause.read_pause(tmp_path)
    assert intent["run_id"]=="shutdown-run"
    assert intent["pause_requested"] is True
    assert intent["manual"] is False


def test_launcher_shutdown_preserves_explicit_manual_pause(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import training_cycle, training_pause
    training_cycle._write_worker_state(
        tmp_path,stage="BUILDING_DATASET",mode="baseline",round_number=0,
        run_id="manually-paused",git_sha="a"*40,
    )
    training_pause.set_pause(tmp_path,run_id="manually-paused",manual=True)
    launcher._request_training_shutdown(tmp_path,timeout_seconds=0)
    assert training_pause.read_pause(tmp_path)["manual"] is True
