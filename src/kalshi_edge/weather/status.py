"""Read-only inspection of an external WeatherEdge collector database.

Do not create or migrate the database in the KalshiEdge web server. The
collector owns the schema and writes. NWP forecasts and METAR observations
must not be confused with independently verified settlement measurements.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
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


def read_weather_status(bootstrap_root: Path, *, preview: bool = False) -> dict:
    """Read aggregated station/source receipts; never imply a prediction exists.

    The SQL is exclusively read-only and uses a fixed local path. No route
    input or query parameter can influence the filesystem location.
    """
    if preview:
        return _empty_status("PREVIEW")
    path = weather_database_path(bootstrap_root)
    if not path.is_file():
        return _empty_status("NOT_FOUND")
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
        return _empty_status("ERROR")
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
    })
    return result
