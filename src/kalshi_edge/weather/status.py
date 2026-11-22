"""Read-only inspection of an external WeatherEdge collector database.

Do not create or migrate the database in the KalshiEdge web server. The
collector owns the schema and writes. NWP forecasts and METAR observations
must not be confused with independently verified settlement measurements.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _empty_status(state: str) -> dict:
    return {
        "mode": "PAPER_ONLY",
        "storage_state": state,
        "forecast_available": False,
        "real_orders_enabled": False,
        "settlement_verification": "NOT_CERTIFIED",
        "sample_count": 0,
        "station_count": 0,
        "last_received_at": None,
        "stations": [],
    }


def weather_database_path(bootstrap_root: Path) -> Path:
    """Separate weather database beside data/bootstrap, under data/weather."""
    return Path(bootstrap_root).parent / "weather" / "weatheredge.sqlite3"


def read_collector_health(bootstrap_root: Path, *, preview: bool = False) -> dict:
    """Read supervisor's atomic local status; never consult credentials/network."""
    if preview:
        return {"state": "PREVIEW", "feeds": {}}
    path = weather_database_path(bootstrap_root).parent / "collector-status.json"
    if not path.is_file():
        return {"state": "NOT_STARTED", "feeds": {}}
    try:
        if path.stat().st_size > 64_000:
            return {"state": "INVALID", "feeds": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("mode") != "DATA_ONLY":
            raise ValueError("bad weather collector health metadata")
        timestamp = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError("collector health timestamp missing timezone")
        age = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        feeds = data.get("feeds")
        if not isinstance(feeds, dict):
            raise ValueError("invalid collector source states")
        return {
            "state": "STALE" if age > timedelta(minutes=15) or age < -timedelta(minutes=2)
                     else "ACTIVE",
            "updated_at": timestamp.isoformat(),
            "decoder": data.get("decoder", {}),
            "settlement": (data.get("settlement", {"state":"NOT_STARTED"})
                           if isinstance(data.get("settlement"),dict)
                           else {"state":"INVALID"}),
            "hourly": (data.get("hourly",{})
                       if isinstance(data.get("hourly"),dict) else {}),
            "hourly_settlement": (data.get("hourly_settlement",{})
                       if isinstance(data.get("hourly_settlement"),dict) else {}),
            "hourly_physics": (data.get("hourly_physics",{})
                       if isinstance(data.get("hourly_physics"),dict) else {}),
            "feeds": {
                source: value for source, value in feeds.items()
                if source in ("METAR", "HRRR", "NBM") and isinstance(value, dict)
            },
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"state": "INVALID", "feeds": {}}


def read_weather_status(bootstrap_root: Path, *, preview: bool = False) -> dict:
    """Read aggregated station/source receipts; never imply a prediction exists.

    The SQL is exclusively read-only and uses a fixed local path. No route
    input or query parameter can influence the filesystem location.
    """
    if preview:
        return {**_empty_status("PREVIEW"),
                "collector": read_collector_health(bootstrap_root, preview=True)}
    collector = read_collector_health(bootstrap_root)
    path = weather_database_path(bootstrap_root)
    if not path.is_file():
        return {**_empty_status("NOT_FOUND"), "collector": collector}
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT station, UPPER(source), COUNT(*), MAX(received_at) "
                "FROM samples WHERE UPPER(source) IN ('HRRR','NBM','METAR') "
                "GROUP BY station, UPPER(source) ORDER BY station, UPPER(source)"
            ).fetchall()
    except (sqlite3.Error, OSError, ValueError):
        return {**_empty_status("ERROR"), "collector": collector}
    stations: dict[str, dict] = {}
    sample_count = 0
    last_received: str | None = None
    for station, source, count, receipt in rows:
        if not isinstance(station, str) or not station:
            continue
        if not isinstance(receipt, str):
            receipt = None
        entry = stations.setdefault(station, {"station": station, "sources": {}})
        entry["sources"][source] = {"count": count, "latest_received_at": receipt}
        sample_count += count
        if receipt is not None and (last_received is None or receipt > last_received):
            last_received = receipt
    result = _empty_status("READY" if sample_count else "EMPTY")
    result.update({
        "sample_count": sample_count,
        "station_count": len(stations),
        "last_received_at": last_received,
        "stations": list(stations.values())[:50],
        "collector": collector,
    })
    return result
