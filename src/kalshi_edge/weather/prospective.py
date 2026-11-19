"""Prospective, write-once, receipt-time-respecting weather forecast journal.

A historical NOAA download is NOT a past prediction. Only forecasts evaluated
while the collector runs enter this journal, without later recomputation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .predictor import STATIONS, _utc, read_station_forecast, station_day

JOURNAL_TABLE = "weather_forecast_snapshots"
MIN_HOUR_STANDARD = 9
MAX_HOUR_STANDARD = 16


def _ensure_journal(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS weather_forecast_snapshots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "station TEXT NOT NULL,"
        "climate_day TEXT NOT NULL,"
        "checkpoint_hour_standard INTEGER NOT NULL,"
        "asof TEXT NOT NULL,"
        "forecast_json TEXT NOT NULL,"
        "payload_sha256 TEXT NOT NULL,"
        "UNIQUE(station,climate_day,checkpoint_hour_standard))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS weather_forecast_snapshots_day "
        "ON weather_forecast_snapshots(station,climate_day,checkpoint_hour_standard)"
    )


def capture_station_snapshots(db_path: Path, *, asof: datetime | None = None,
                              stations: tuple[str, ...] = tuple(STATIONS)) -> dict:
    """Append the first usable hourly forecast to the local SQLite journal.

    No access to future observations and no overwrite of the earliest
    forecast in each station-local-standard-time hour. This function does not
    infer settlement, calibration or Kalshi contract odds.
    """
    asof = _utc(asof or datetime.now(timezone.utc))
    if not stations or len(set(stations)) != len(stations) or any(s not in STATIONS for s in stations):
        raise ValueError("unknown or duplicated forecast station")
    output: dict = {
        "mode": "PAPER_ONLY", "inserted": 0,
        "statuses": {}, "asof": asof.isoformat(),
    }
    if not Path(db_path).is_file():
        output["statuses"] = {s: "MISSING_DATABASE" for s in stations}
        return output
    proposed: list[tuple] = []
    for station in stations:
        climate_day, start, end, peak = station_day(STATIONS[station], asof)
        hour = int((asof - start).total_seconds() // 3600)
        if not MIN_HOUR_STANDARD <= hour <= MAX_HOUR_STANDARD:
            output["statuses"][station] = "OUTSIDE_FORECAST_WINDOW"
            continue
        forecast = read_station_forecast(db_path, station, asof)
        output["statuses"][station] = forecast["status"]
        if forecast["status"] != "READY_RESEARCH":
            continue
        if forecast["settlement_verified"] or forecast["tradable"]:
            raise ValueError("research forecast must not be treated as tradable")
        if not (forecast["asof"] == asof.isoformat() and
                forecast["climate_day"] == climate_day.isoformat()):
            raise ValueError("forecast timestamp provenance mismatch")
        # The as-of correctness of all inputs is checked in the predictor,
        # but the immutable journal rechecks receipt times before committing.
        receipts = [forecast.get("metar_received_at")]
        receipts += list(forecast.get("nwp_receipts_utc", {}).values())
        if len(receipts) != 3 or any(not isinstance(x, str) or
                                    _utc(datetime.fromisoformat(x)) > asof
                                    for x in receipts):
            raise ValueError("forecast contains future or missing receipts")
        payload = json.dumps(forecast, sort_keys=True, allow_nan=False,
                             separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        proposed.append((station, climate_day.isoformat(), hour,
                         asof.isoformat(), payload, digest))
    # No writes if no current, fully observed model distribution exists.
    if not proposed:
        return output
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_journal(conn)
        for item in proposed:
            result = conn.execute(
                "INSERT OR IGNORE INTO weather_forecast_snapshots("
                "station,climate_day,checkpoint_hour_standard,"
                "asof,forecast_json,payload_sha256) VALUES(?,?,?,?,?,?)",
                item,
            )
            output["inserted"] += int(result.rowcount)
        conn.commit()
    return output


def read_station_snapshots(db_path: Path, station: str | None = None,
                           *, limit: int = 100_000) -> list[dict]:
    """Read frozen records, not the present model reconstructed for old days."""
    if station is not None and station not in STATIONS:
        raise ValueError("unknown forecast station")
    if not Path(db_path).is_file():
        return []
    limit = min(100_000, max(1, int(limit)))
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            sql = ("SELECT station,climate_day,checkpoint_hour_standard,"
                   "asof,forecast_json,payload_sha256 "
                   "FROM weather_forecast_snapshots")
            params = ()
            if station:
                sql += " WHERE station=?"
                params = (station,)
            sql += " ORDER BY climate_day,checkpoint_hour_standard,station LIMIT " + str(limit)
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    outputs = []
    for station_code, day, hour, asof, raw, saved_hash in rows:
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != saved_hash:
            raise ValueError("weather forecast journal checksum mismatch")
        forecast = json.loads(raw)
        if forecast.get("station") != station_code or forecast.get("asof") != asof:
            raise ValueError("weather journal metadata mismatch")
        outputs.append({
            "station": station_code, "climate_day": day,
            "checkpoint_hour_standard": hour,
            "asof": asof, "forecast": forecast,
            "payload_sha256": saved_hash,
        })
    return outputs


def journal_summary(db_path: Path) -> dict:
    """Safe dashboard summary of prospective snapshots, no accuracy claims."""
    if not Path(db_path).is_file():
        return {"recorded_snapshots": 0, "independent_station_days": 0,
                "recorded_noon_snapshots": 0}
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            total, unique_days, noon = conn.execute(
                "SELECT COUNT(*),COUNT(DISTINCT station || ':' || climate_day),"
                "SUM(CASE WHEN checkpoint_hour_standard=12 THEN 1 ELSE 0 END) "
                "FROM weather_forecast_snapshots"
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {"recorded_snapshots": 0, "independent_station_days": 0,
                    "recorded_noon_snapshots": 0}
        raise
    return {"recorded_snapshots": total, "independent_station_days": unique_days,
            "recorded_noon_snapshots": noon or 0}
