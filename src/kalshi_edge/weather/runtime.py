"""Managed WeatherEdge data collector for `kalshiedge start`.

The worker runs in a child process owned by the one-command supervisor.
It never sends Kalshi orders, never accesses credentials, and never modifies
Bitcoin/Gold models. Missing GRIB dependencies are installed once on first
launch from declared binary wheels, without blocking the BTC dashboard.

Health is written atomically so a failed feed is visible in the Weather UI.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .collection import collect_metars_once
from .nwp_collection import collect_recent
from .prospective import capture_station_snapshots
from .settlement_collector import poll_settlements
from .hourly import collect_hourly_once, journal_hourly_forecasts, settle_hourly_once
from .hourly_physics import collect_hourly_physics_once
from .hourly_physical_learning import (
    journal_physical_pairs, freeze_candidate_locks,
)
from .hourly_quotes import collect_hourly_quotes_once
from .status import weather_database_path


DECODER_MODULES = ("numpy", "xarray", "cfgrib", "eccodes")
WEATHER_WHEELS = (
    "numpy>=2.5,<3",
    "xarray>=2024.6",
    "cfgrib>=0.9.15",
    "eccodes>=2.38",
)
METAR_SECONDS = 300
NWP_SECONDS = 1800
FORECAST_JOURNAL_SECONDS = 600
SETTLEMENT_POLL_SECONDS = 6 * 3600
HOURLY_INDEX_SECONDS = 120
HOURLY_SETTLE_SECONDS = 300
HOURLY_QUOTES_SECONDS = 20
PHYSICAL_NWP_SECONDS = 3600


def _missing_decoder_modules() -> list[str]:
    return [name for name in DECODER_MODULES if importlib.util.find_spec(name) is None]


def ensure_weather_decoder(root: Path) -> dict:
    """Set up optional binary decoder wheels in a private weather directory.

    Weather dependencies never install into the existing BTC/training virtual
    environment. The isolated wheel directory is added only to this weather
    worker process, and is reused on subsequent starts for the same Python
    major/minor. KALSHIEDGE_WEATHER_AUTOINSTALL=0 disables fresh installs.
    """
    root = Path(root).resolve()
    target = root / "data" / "weather" / (
        f"decoder-py{sys.version_info.major}{sys.version_info.minor}"
    )
    if target.is_dir() and str(target) not in sys.path:
        sys.path.insert(0, str(target))
        importlib.invalidate_caches()
    missing = _missing_decoder_modules()
    if not missing:
        return {"state": "AVAILABLE"}
    if os.environ.get("KALSHIEDGE_WEATHER_AUTOINSTALL", "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return {"state": "DISABLED", "reason": "Automatic GRIB wheel installation disabled"}
    target.mkdir(parents=True, exist_ok=True)
    print(
        f"WeatherEdge: Installing optional NOAA GRIB decoder wheels into "
        f"isolated weather directory {target}. BTC environment is unchanged.",
        flush=True,
    )
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "--no-input", "--only-binary=:all:",
        "--target", str(target), "--upgrade", *WEATHER_WHEELS,
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(root), timeout=240,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "state": "INSTALL_FAILED",
            "reason": f"Weather dependency setup unavailable: {type(exc).__name__}",
        }
    if result.returncode != 0:
        return {
            "state": "INSTALL_FAILED",
            "reason": f"Weather decoder wheel installation exited {result.returncode}; "
                      "inspect the weather supervisor log",
        }
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    importlib.invalidate_caches()
    remains = _missing_decoder_modules()
    if remains:
        return {
            "state": "INSTALL_FAILED",
            "reason": "Installed packages were not importable: " + ", ".join(remains),
        }
    return {"state": "AVAILABLE"}


def collector_health_path(db_path: Path) -> Path:
    return Path(db_path).parent / "collector-status.json"


class WeatherSupervisor:
    """Two independent, failure-isolated polling loops.

    NWP decoder setup runs in a background thread; METAR polling is never
    blocked by the optional package installation. Every exception is
    recorded, never mistaken for a valid forecast.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        metar_collect: Callable[[Path], Awaitable[dict]] = collect_metars_once,
        nwp_collect: Callable[[Path], Awaitable[dict]] = collect_recent,
        ensure_decoder: Callable[[], dict] | None = None,
        forecast_capture: Callable[[Path], dict] = capture_station_snapshots,
        settlement_collect: Callable[[Path], Awaitable[dict]] = poll_settlements,
        hourly_collect: Callable[[Path], Awaitable[dict]] = collect_hourly_once,
        hourly_settle: Callable[[Path], Awaitable[dict]] = settle_hourly_once,
        hourly_physics_collect: Callable[[Path], Awaitable[dict]] = collect_hourly_physics_once,
        hourly_quotes_collect: Callable[[Path], Awaitable[dict]] = collect_hourly_quotes_once,
    ):
        self.db_path = Path(db_path)
        self.status_path = collector_health_path(self.db_path)
        self._collect_metar = metar_collect
        self._collect_nwp = nwp_collect
        self._capture_forecasts = forecast_capture
        self._collect_settlement = settlement_collect
        self._collect_hourly = hourly_collect
        self._settle_hourly = hourly_settle
        self._collect_hourly_physics = hourly_physics_collect
        self._collect_hourly_quotes = hourly_quotes_collect
        self._hourly_quotes = {"state":"STARTING"}
        self._hourly_physics = {"state":"STARTING"}
        self._hourly = {city: {"state":"STARTING"} for city in
                        ("chicago","nyc","la-coastal","miami")}
        self._hourly_settlement = {"state":"STARTING","verified":0}
        self._settlement = {"state": "STARTING", "crosschecked": 0}
        self._forecast_journal = {"state": "STARTING", "inserted": 0}
        self._ensure_decoder = ensure_decoder or (
            lambda: ensure_weather_decoder(Path.cwd())
        )
        self._decoder_checked = False
        self._decoder_state: dict | None = None
        self._feeds = {
            name: {"state": "STARTING"} for name in ("METAR", "HRRR", "NBM")
        }
        self._persist()

    def _persist(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "mode": "DATA_ONLY",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "feeds": self._feeds,
            "decoder": self._decoder_state or {"state": "NOT_CHECKED"},
            "forecast_journal": self._forecast_journal,
            "settlement": self._settlement,
            "hourly": self._hourly,
            "hourly_settlement": self._hourly_settlement,
            "hourly_physics": self._hourly_physics,
            "hourly_quotes": self._hourly_quotes,
            "real_orders_enabled": False,
        }
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.status_path.parent, mode="w",
                encoding="utf-8", prefix=".collector-", suffix=".tmp",
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(state, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.status_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _mark(self, name: str, state: str, *, detail: str | None = None,
              receipt: str | None = None) -> None:
        record = {
            "state": state,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if detail:
            record["reason"] = detail[:350]
        if receipt:
            record["last_received_at"] = receipt
        self._feeds[name] = record
        self._persist()

    async def poll_metar(self) -> None:
        try:
            report = await self._collect_metar(self.db_path)
            if not isinstance(report, dict) or report.get("mode") != "DATA_ONLY":
                raise ValueError("unexpected METAR collection response")
            state = "NO_RECENT_DATA" if report.get("no_recent_data") else "READY"
            self._mark("METAR", state, receipt=report.get("received_at"))
        except Exception as exc:
            # Do not let network or malformed observations stop NBM/HRRR.
            self._mark("METAR", "UNAVAILABLE",
                       detail=f"{type(exc).__name__}: {exc}")
        print(json.dumps({"weather_feed": "METAR", **self._feeds["METAR"]}),
              flush=True)

    async def poll_nwp(self) -> None:
        if not self._decoder_checked:
            self._decoder_checked = True
            try:
                self._decoder_state = await asyncio.to_thread(
                    self._ensure_decoder
                )
            except Exception as exc:
                self._decoder_state = {
                    "state": "INSTALL_FAILED",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            self._persist()
        if (not isinstance(self._decoder_state, dict)
                or self._decoder_state.get("state") != "AVAILABLE"):
            reason = (self._decoder_state or {}).get(
                "reason", "GRIB decoder unavailable")
            for model in ("HRRR", "NBM"):
                self._mark(model, "DEPENDENCIES_UNAVAILABLE", detail=reason)
            return
        try:
            report = await asyncio.wait_for(
                self._collect_nwp(self.db_path), timeout=420
            )
            if not isinstance(report, dict):
                raise ValueError("unexpected NWP collection response")
            for model in ("HRRR", "NBM"):
                item = report.get(model)
                if not isinstance(item, dict):
                    self._mark(model, "UNAVAILABLE",
                               detail="NOAA model was not returned")
                elif item.get("mode") == "DATA_ONLY":
                    self._mark(model, "READY",
                               receipt=item.get("received_at"))
                else:
                    self._mark(model, "UNAVAILABLE",
                               detail=str(item.get("reason", "NOAA model unavailable")))
        except Exception as exc:
            for model in ("HRRR", "NBM"):
                self._mark(model, "UNAVAILABLE",
                           detail=f"{type(exc).__name__}: {exc}")
        print(json.dumps({
            "weather_feed": "NWP",
            "HRRR": self._feeds["HRRR"], "NBM": self._feeds["NBM"],
        }), flush=True)

    async def poll_forecasts(self) -> None:
        """Record only forecasts whose exact event/receipt-time inputs exist.

        This is independent of METAR/NWP loops. A missing model or failed
        write is a health status, never a fabricated forecast or label.
        """
        try:
            status = await asyncio.to_thread(self._capture_forecasts, self.db_path)
            if not isinstance(status, dict) or status.get("mode") != "PAPER_ONLY":
                raise ValueError("invalid prospective journal result")
            self._forecast_journal = {
                "state": "READY" if status.get("inserted", 0) else "WAITING",
                "inserted": int(status["inserted"]),
                "stations": status.get("statuses", {}),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            self._forecast_journal = {
                "state": "UNAVAILABLE",
                "reason": f"{type(exc).__name__}: {exc}"[:350],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        self._persist()

    async def poll_settlements(self) -> None:
        """Collect source-verified outcomes only after prior climate days end.

        No Kalshi order API or trading credentials are ever accessed. Missing,
        preliminary, inconsistent or changed provider data is marked
        unverified and never promoted into calibration outcomes.
        """
        try:
            report = await asyncio.wait_for(
                self._collect_settlement(self.db_path), timeout=480
            )
            if (not isinstance(report, dict)
                    or report.get("mode") != "DATA_ONLY"):
                raise ValueError("unexpected settlement collector response")
            self._settlement = {
                "state": report.get("state", "UNVERIFIED"),
                "crosschecked": int(report.get("crosschecked", 0)),
                "unverified": int(report.get("unverified", 0)),
                "details": report.get("details", [])[:20],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            self._settlement = {
                "state": "UNAVAILABLE",
                "reason": f"{type(exc).__name__}: {exc}"[:250],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        self._persist()

    async def poll_hourly(self) -> None:
        """Run official Kalshi index collection and frozen hourly predictions.

        This intentionally replaces daily-high foreground collection. Old
        database tables are retained but not used as hourly market targets.
        """
        try:
            report = await asyncio.wait_for(
                self._collect_hourly(self.db_path), timeout=120)
            if not isinstance(report,dict) or report.get("mode")!="HOURLY_PAPER_ONLY":
                raise ValueError("unexpected hourly index collector response")
            self._hourly = report.get("cities",{})
            if not isinstance(self._hourly,dict):
                raise ValueError("hourly sources missing")
            frozen = await asyncio.to_thread(journal_hourly_forecasts,self.db_path)
            self._hourly["journal"]={"state":"READY",
                                     "new_forecasts":frozen["inserted"]}
            # Research pairs do not depend on contract availability. Keep this
            # separate from the official-index health path if SQLite is busy.
            try:
                pairs=await asyncio.to_thread(journal_physical_pairs,self.db_path)
                self._hourly["physical_pairs"]={
                    "state":"COLLECTING",
                    "new_pairs":pairs["inserted"],
                    "checked_at":datetime.now(timezone.utc).isoformat()}
            except Exception as exc:
                self._hourly["physical_pairs"]={
                    "state":"UNAVAILABLE",
                    "reason":f"{type(exc).__name__}: {exc}"[:180]}
        except Exception as exc:
            self._hourly={"state":"UNAVAILABLE",
                          "reason":f"{type(exc).__name__}: {exc}"[:220]}
        self._persist()

    async def poll_hourly_physics(self) -> None:
        """Official station configs + physically sampled NOAA guidance.

        An independently scheduled research worker never blocks the official
        index collector or updates live YES/NO probabilities.
        """
        try:
            if not self._decoder_checked:
                self._decoder_checked = True
                try:
                    self._decoder_state = await asyncio.to_thread(self._ensure_decoder)
                except Exception as exc:
                    self._decoder_state = {
                        "state":"INSTALL_FAILED",
                        "reason":f"{type(exc).__name__}: {exc}"[:200]}
            if (not isinstance(self._decoder_state,dict) or
                self._decoder_state.get("state")!="AVAILABLE"):
                self._hourly_physics={
                    "state":"DECODER_UNAVAILABLE",
                    "reason":(self._decoder_state or {}).get(
                        "reason","GRIB decoder unavailable")[:190]}
                return
            report = await asyncio.wait_for(
                self._collect_hourly_physics(self.db_path),timeout=240)
            if (not isinstance(report,dict) or
                report.get("mode")!="HOURLY_PHYSICAL_RESEARCH" or
                report.get("real_orders_enabled") is not False):
                raise ValueError("invalid physical research collector result")
            self._hourly_physics={
                "state":"RESEARCH_READY" if any(
                    isinstance(x,dict) and x.get("state")=="RESEARCH_READY"
                    for x in report.get("cities",{}).values()) else "UNAVAILABLE",
                "cities":report.get("cities",{}),
                "model_status":report.get("model_status",{}),
                "target_utc":report.get("target_utc"),
                "checked_at":datetime.now(timezone.utc).isoformat(),
            }
            # Freeze prospective candidates only in the write-owning worker.
            # No GUI request may train or rewrite a candidate retrospectively.
            try:
                pairs=await asyncio.to_thread(journal_physical_pairs,self.db_path)
                locks=await asyncio.to_thread(freeze_candidate_locks,self.db_path)
                self._hourly_physics["adaptive"]={
                    "state":"MONITORING","new_pairs":pairs["inserted"],
                    "new_candidate_locks":locks["locked"]}
            except Exception as exc:
                self._hourly_physics["adaptive"]={
                    "state":"UNAVAILABLE",
                    "reason":f"{type(exc).__name__}: {exc}"[:180]}
        except Exception as exc:
            self._hourly_physics={
                "state":"UNAVAILABLE",
                "reason":f"{type(exc).__name__}: {exc}"[:220]}
        finally:
            self._persist()

    async def poll_hourly_quotes(self) -> None:
        """Independent price evidence never blocks the official index feed."""
        try:
            report=await asyncio.wait_for(
                self._collect_hourly_quotes(self.db_path),timeout=65)
            if (not isinstance(report,dict) or
                report.get("mode")!="HOURLY_QUOTES_READ_ONLY" or
                report.get("real_orders_enabled") is not False):
                raise ValueError("invalid read-only Kalshi quote response")
            self._hourly_quotes={
                "state":report.get("state","READY" if report.get("valid_asks") else "UNAVAILABLE"),
                "checked":int(report.get("checked",0)),
                "valid_asks":int(report.get("valid_asks",0)),
                "no_visible_ask":int(report.get("no_visible_ask",0)),
                "unavailable":int(report.get("unavailable",0)),
                "errors":report.get("errors",[])[:4],
                "checked_at":datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            self._hourly_quotes={"state":"UNAVAILABLE",
                "reason":f"{type(exc).__name__}: {exc}"[:190]}
        self._persist()

    async def poll_hourly_settlements(self) -> None:
        try:
            report=await asyncio.wait_for(
                self._settle_hourly(self.db_path),timeout=180)
            if not isinstance(report,dict) or report.get("mode")!="HOURLY_PAPER_ONLY":
                raise ValueError("unexpected hourly settlement result")
            self._hourly_settlement={"state":"READY",
                     "verified":int(report.get("verified",0)),
                     "unverified":int(report.get("unverified",0)),
                     "errors":report.get("errors",[])[:8]}
        except Exception as exc:
            self._hourly_settlement={"state":"UNAVAILABLE",
                   "reason":f"{type(exc).__name__}: {exc}"[:220]}
        self._persist()

    async def run_forever(self) -> None:
        async def every(poll, period: int) -> None:
            while True:
                await poll()
                await asyncio.sleep(period)

        # Hourly-only product. Legacy daily research code remains importable
        # for reproducible archives but is NEVER scheduled by kalshiedge start.
        await asyncio.gather(
            every(self.poll_hourly, HOURLY_INDEX_SECONDS),
            every(self.poll_hourly_quotes, HOURLY_QUOTES_SECONDS),
            every(self.poll_hourly_settlements, HOURLY_SETTLE_SECONDS),
            every(self.poll_hourly_physics, PHYSICAL_NWP_SECONDS),
        )


def main() -> int:
    """Started exclusively as an owned launcher process by default."""
    from ..bootstrap.config import BootstrapSettings
    from ..launcher import SingleStarter

    db = weather_database_path(BootstrapSettings().bootstrap_dir)
    try:
        with SingleStarter(db.parent / "collector.lock"):
            asyncio.run(WeatherSupervisor(db).run_forever())
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(f"WeatherEdge supervisor not started: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
