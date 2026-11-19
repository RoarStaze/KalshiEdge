"""One-command weather collection lifecycle: no separate PowerShell sessions."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kalshi_edge import launcher
from kalshi_edge.weather import runtime
from kalshi_edge.weather.status import read_weather_status


class DummyProcess:
    def __init__(self,pid):
        self.pid = pid
        self.returncode = None
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True
        self.returncode = -15

    def wait(self,timeout=None):
        return self.returncode

    def kill(self):
        self.terminate()


def _launcher_monkeys(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher,"BootstrapSettings",
        lambda:SimpleNamespace(bootstrap_dir=tmp_path/"data"/"bootstrap"))
    monkeypatch.setattr(launcher,"load_default_bundle",
        lambda root:SimpleNamespace(model_version="test",bundle_sha256="a"*64))
    monkeypatch.setattr(launcher,"_dashboard_healthy",lambda port:True)
    monkeypatch.setattr(launcher,"_dashboard_running_build",lambda port:launcher.dashboard_build_id())
    monkeypatch.setattr(launcher,"_request_training_shutdown",lambda root:False)
    def stop_at_loop(seconds):
        if seconds == 1.0:
            raise KeyboardInterrupt
    monkeypatch.setattr(launcher.time,"sleep",stop_at_loop)


def test_one_start_autolaunches_weather_and_cleans_up(tmp_path,monkeypatch):
    _launcher_monkeys(monkeypatch,tmp_path)
    procs = []
    def spawn(args,root,log):
        p = DummyProcess(9000+len(procs))
        procs.append((args,p))
        return p
    monkeypatch.setattr(launcher,"_spawn_service",spawn)
    assert launcher.start(open_browser=False)==0
    assert [args for args,_ in procs]==[["predict-live"],["weather-watch"]]
    assert all(p.stopped for _,p in procs)


def test_weather_process_failure_does_not_stop_btc(tmp_path,monkeypatch,capsys):
    _launcher_monkeys(monkeypatch,tmp_path)
    procs=[]
    def spawn(args,root,log):
        p=DummyProcess(9200+len(procs))
        if args[0]=="weather-watch":
            p.stopped=True
            p.returncode=2
        procs.append((args,p))
        return p
    monkeypatch.setattr(launcher,"_spawn_service",spawn)
    assert launcher.start(open_browser=False)==0
    assert any(args==["weather-watch"] for args,p in procs)
    assert "Weather" in capsys.readouterr().err


def test_weather_runtime_writes_source_health_for_failed_nwp(tmp_path):
    db=tmp_path/"weather"/"weatheredge.sqlite3"
    async def metar(path):
        assert path==db
        return {"inserted":1,"mode":"DATA_ONLY"}
    async def nwp(path):
        assert path==db
        return {"HRRR":{"mode":"DATA_ONLY","points":12},
                "NBM":{"status":"UNAVAILABLE","reason":"NOAA HTTP 404"}}
    state=runtime.WeatherSupervisor(
        db,metar_collect=metar,nwp_collect=nwp,
        ensure_decoder=lambda:{"state":"AVAILABLE"},
    )
    async def first():
        await asyncio.gather(state.poll_metar(),state.poll_nwp())
    asyncio.run(first())
    saved=json.loads((db.parent/"collector-status.json").read_text())
    assert saved["mode"]=="DATA_ONLY"
    assert saved["feeds"]["METAR"]["state"]=="READY"
    assert saved["feeds"]["HRRR"]["state"]=="READY"
    assert saved["feeds"]["NBM"]["state"]=="UNAVAILABLE"
    assert saved["feeds"]["NBM"]["reason"]=="NOAA HTTP 404"
    status=read_weather_status(tmp_path/"bootstrap")
    assert status["collector"]["feeds"]["NBM"]["state"]=="UNAVAILABLE"
    assert status["real_orders_enabled"] is False


def test_weather_runtime_keeps_metar_running_when_decoder_unavailable(tmp_path):
    db=tmp_path/"weather"/"weatheredge.sqlite3"
    async def metar(path):
        return {"mode":"DATA_ONLY","inserted":2}
    nwp=Mock(side_effect=AssertionError("NWP must not run without decoder"))
    sup=runtime.WeatherSupervisor(db,metar_collect=metar,nwp_collect=nwp,
            ensure_decoder=lambda:{"state":"INSTALL_FAILED","reason":"offline"})
    async def run():
        await asyncio.gather(sup.poll_metar(),sup.poll_nwp())
    asyncio.run(run())
    data=json.loads((db.parent/"collector-status.json").read_text())
    assert data["feeds"]["METAR"]["state"]=="READY"
    assert data["feeds"]["NBM"]["state"]=="DEPENDENCIES_UNAVAILABLE"
    assert data["feeds"]["HRRR"]["state"]=="DEPENDENCIES_UNAVAILABLE"
    assert nwp.call_count==0


def test_decoder_bootstrap_uses_fixed_binary_packages_and_can_be_disabled(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime,"_missing_decoder_modules",lambda:["cfgrib"])
    monkeypatch.setenv("KALSHIEDGE_WEATHER_AUTOINSTALL","0")
    assert runtime.ensure_weather_decoder(tmp_path)["state"]=="DISABLED"
    monkeypatch.setenv("KALSHIEDGE_WEATHER_AUTOINSTALL","1")
    invoked=[]
    def install(args,**kwargs):
        invoked.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout="installed",stderr="")
    monkeypatch.setattr(runtime.subprocess,"run",install)
    monkeypatch.setattr(runtime,"_missing_decoder_modules",lambda:[] if invoked else ["cfgrib"])
    result=runtime.ensure_weather_decoder(tmp_path)
    assert result["state"]=="AVAILABLE"
    assert len(invoked)==1
    command,kwargs=invoked[0]
    assert command[:4]==[runtime.sys.executable,"-m","pip","install"]
    assert "--only-binary=:all:" in command
    assert "--target" in command
    target = Path(command[command.index("--target") + 1])
    assert target.is_relative_to(tmp_path / "data" / "weather")
    assert kwargs["timeout"] <= 240
    assert kwargs["shell"] is False


def test_test_preview_never_launches_weather(monkeypatch,tmp_path):
    from kalshi_edge.bootstrap import preview_site
    monkeypatch.setattr(launcher,"BootstrapSettings",
        lambda:SimpleNamespace(bootstrap_dir=tmp_path/"bootstrap"))
    monkeypatch.setattr(preview_site,"run_test_preview",lambda **kw:0)
    monkeypatch.setattr(launcher,"_spawn_service",Mock(side_effect=AssertionError("no background workers")))
    assert launcher.main(["test","--no-browser"])==0
