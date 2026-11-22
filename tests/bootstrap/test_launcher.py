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
    fake_weather = FakeProcess(102)

    def spawn(args, root, log):
        spawned.append(args)
        return {
            "final-history": fake_history,
            "predict-live": fake_predictor,
            "weather-watch": fake_weather,
        }[args[0]]

    monkeypatch.setattr(launcher, "_spawn_service", spawn)
    monkeypatch.setattr(launcher, "_dashboard_healthy", lambda port: len(spawned) >= 1)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)

    def short_sleep(seconds):
        if seconds == 1.0:
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher.time, "sleep", short_sleep)
    assert launcher.start() == 0
    assert spawned == [
        ["final-history", "--port", "8899"], ["predict-live"], ["weather-watch"],
    ]
    assert opened == ["http://127.0.0.1:8899/"]
    assert fake_history.stopped
    assert fake_predictor.stopped
    assert fake_weather.stopped


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
    monkeypatch.setattr(launcher, "_dashboard_running_build",
                        lambda port: launcher.dashboard_build_id())
    monkeypatch.setattr(launcher, "_tcp_busy", Mock(side_effect=AssertionError("should reuse healthy server")))
    spawned = []
    fake_predictor = FakeProcess(101)
    monkeypatch.setattr(launcher, "_spawn_service", lambda args, root, log: spawned.append(args) or fake_predictor)

    def short_sleep(seconds):
        if seconds == 1.0:
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher.time, "sleep", short_sleep)
    assert launcher.start(open_browser=False) == 0
    assert spawned == [["predict-live"], ["weather-watch"]]
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


def test_launcher_shutdown_preserves_frozen_stage4_child_for_reopen(tmp_path,monkeypatch):
    """Killing the dashboard process tree would kill our safely frozen worker."""
    from kalshi_edge.bootstrap import training_cycle, training_pause
    training_cycle._write_worker_state(
        tmp_path,stage="TRAINING",mode="baseline",round_number=0,
        run_id="close-run",git_sha="a"*40,worker_pid=44282,
        worker_identity="start-44282",
    )
    training_pause.reset_pause(tmp_path,run_id="close-run")
    observed=[]
    def freeze(root,run,*,manual):
        observed.append((run["worker_pid"],manual))
        training_pause.set_pause(root,run_id="close-run",manual=manual)
        training_cycle._write_worker_state(
            root,stage="PAUSED",mode="baseline",round_number=0,
            resume_stage="TRAINING",suspended_in_memory=True,
        )
        return True
    monkeypatch.setattr(training_cycle,"_suspend_training_fit",freeze)
    assert launcher._request_training_shutdown(tmp_path,timeout_seconds=0) is True
    assert observed==[(44282,False)]
    assert training_pause.read_pause(tmp_path)["manual"] is False
    assert training_cycle._run_state(tmp_path)["suspended_in_memory"] is True


def test_preserving_suspended_worker_stops_only_dashboard_not_children(monkeypatch):
    process=FakeProcess(13001)
    calls=[]
    monkeypatch.setattr(launcher.os,"killpg",
                        lambda *args:calls.append("killpg") or
                        pytest.fail("must not terminate frozen worker's process group"))
    launcher._stop(process,preserve_children=True)
    assert process.stopped
    assert calls==[]


def test_test_command_runs_only_preview_without_production_services(tmp_path, monkeypatch):
    """Second PowerShell preview never starts the production supervisor."""
    from kalshi_edge.bootstrap import preview_site
    from types import SimpleNamespace
    monkeypatch.setattr(launcher, "BootstrapSettings",
                        lambda: SimpleNamespace(bootstrap_dir=tmp_path / "live-data"))
    called=[]
    monkeypatch.setattr(preview_site, "run_test_preview",
                        lambda **kwargs: called.append(kwargs) or 0)
    monkeypatch.setattr(launcher, "start", Mock(side_effect=AssertionError("must not start live service")))
    monkeypatch.setattr(launcher, "_request_training_shutdown",
                        Mock(side_effect=AssertionError("must not pause live trainer")))
    assert launcher.main(["test","--no-browser"])==0
    assert called==[{
        "port":8900,"open_browser":False,
        "asset_path":tmp_path/"live-data"/"assets"/"kalshiedge-neon-city.webp",
    }]
    assert launcher.main(["test","--no-browser","--port","8901"])==0
    assert called[-1]["port"]==8901


def test_dashboard_running_build_detects_old_health_without_version(monkeypatch):
    """A prior dashboard can pass identity checks yet serve stale Python modules."""
    class OldResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, *_):
            return b'{"app":"kalshiedge","service":"unified-dashboard","ready":true}'
    monkeypatch.setattr(launcher.urllib.request, "urlopen",
                        lambda req, timeout: OldResponse())
    assert launcher._dashboard_healthy(8899)
    assert launcher._dashboard_running_build(8899) is None


def test_dashboard_running_build_reads_current_build_marker(monkeypatch):
    expected = "1234567890abcdef"
    class NewResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, *_):
            return json.dumps({"app":"kalshiedge","service":"unified-dashboard",
                               "ready":True,"dashboard_build_id":expected}).encode()
    monkeypatch.setattr(launcher.urllib.request, "urlopen",
                        lambda req, timeout: NewResponse())
    assert launcher._dashboard_running_build(8899) == expected


def test_launcher_refuses_stale_unified_dashboard_without_starting_predictor(
        tmp_path, monkeypatch, capsys):
    """Never silently reuse a healthy but outdated server after git pull."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher,"BootstrapSettings",
                        lambda: SimpleNamespace(bootstrap_dir=tmp_path / "bootstrap"))
    monkeypatch.setattr(launcher,"load_default_bundle",
                        lambda root: SimpleNamespace(model_version="v5",bundle_sha256="a"*64))
    monkeypatch.setattr(launcher,"_dashboard_healthy",lambda port:True)
    monkeypatch.setattr(launcher,"_dashboard_running_build",lambda port:None)
    monkeypatch.setattr(launcher,"_spawn_service",
                        Mock(side_effect=AssertionError("must not spawn on stale server")))
    assert launcher.start(open_browser=False) == 2
    out=capsys.readouterr()
    assert "outdated" in out.err.lower() or "stale" in out.err.lower()
    assert "Ctrl+C" in out.err


def test_launcher_version_changes_when_paper_source_changes_without_git_commit(tmp_path):
    """Build signature must use source bytes, not a potentially stale git HEAD."""
    from kalshi_edge.bootstrap.runtime_identity import source_build_id
    x=tmp_path/"scalp_paper_ui.py"
    y=tmp_path/"paper_scalper.py"
    x.write_bytes(b"OLD_HEAD")
    y.write_bytes(b"WAITING_FOR_VERIFIED_69_PERCENT_FINAL")
    first=source_build_id([x,y])
    y.write_bytes(b"INTRAROUND_UP_DOWN_BOOK_MOMENTUM")
    second=source_build_id([x,y])
    assert len(first) == len(second) == 16
    assert first != second


def test_storage_command_does_not_launch_trading_or_training(tmp_path, monkeypatch, capsys):
    """The storage command is standalone, offline and read-only by default."""
    preserved = tmp_path / "data" / "bootstrap" / "derived" / "features.parquet"
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b"training evidence")
    disposable = tmp_path / "src" / "__pycache__" / "file.pyc"
    disposable.parent.mkdir(parents=True)
    disposable.write_bytes(b"cache")
    monkeypatch.setattr(launcher, "start",
                        Mock(side_effect=AssertionError("must not start live service")))
    monkeypatch.setattr(launcher, "_request_training_shutdown",
                        Mock(side_effect=AssertionError("must not touch training workers")))
    assert launcher.main(["storage", "scan", "--root", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cache_bytes"] == len(b"cache")
    assert report["total_bytes"] == len(b"cache") + len(b"training evidence")
    assert preserved.exists()
    assert disposable.exists()
