"""NOAA AWC METAR collector with true event and receipt timestamps.

NOAA temperature observations are an input to weather research and are NOT
automatically equivalent to any Kalshi contract's settlement publisher.
Only read-only NOAA HTTP requests occur. SQLite writes are local only.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .predictor import STATIONS, _utc

AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"
MIN_POLL_SECONDS = 300  # Respect AWC published API usage limits.


def parse_awc_metar(row: dict, received_at: datetime) -> dict:
    """Decode Celsius and timestamp; reject unknown station/missing attributes."""
    if not isinstance(row, dict):
        raise TypeError("METAR response must be an object")
    station = row["icaoId"]
    if not isinstance(station, str) or station not in STATIONS:
        raise ValueError("unrecognized station")
    raw_time = row["obsTime"]
    if isinstance(raw_time, bool):
        raise ValueError("invalid METAR timestamp")
    if isinstance(raw_time, (int, float)):
        if not math.isfinite(raw_time):
            raise ValueError("invalid METAR timestamp")
        event = datetime.fromtimestamp(raw_time, tz=timezone.utc)
    elif isinstance(raw_time, str):
        event = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    else:
        raise ValueError("missing METAR timestamp")
    event, received = _utc(event), _utc(received_at)
    temp = row["temp"]
    if isinstance(temp, bool):
        raise ValueError("invalid METAR temperature")
    value = float(temp)
    if not math.isfinite(value) or not -90 < value < 60:
        raise ValueError("invalid METAR Celsius temperature")
    if event > received:
        raise ValueError("METAR observation in the future")
    if (received - event).total_seconds() > 86400:
        raise ValueError("stale AWC observation")
    return {
        "station": station, "source": "METAR",
        "event_time": event, "received_at": received,
        "temp_f": round(value * 9 / 5 + 32, 4),
        "raw": str(row.get("rawOb", ""))[:2000],
    }


async def collect_metars_once(
    db_path: Path, *, client: httpx.AsyncClient | None = None,
    stations: tuple[str, ...] = tuple(STATIONS),
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Fetch current observations once, then append at actual receipt time.

    Missing/unusable station observations are counted as rejected, not inferred.
    Idempotent by (station, source, event_time) within the local database.
    """
    if not stations or any(station not in STATIONS for station in stations):
        raise ValueError("only configured airport stations are supported")
    if len(set(stations)) != len(stations):
        raise ValueError("duplicate station")
    clock = clock or (lambda: datetime.now(timezone.utc))
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=30, follow_redirects=False,
            headers={"User-Agent": "KalshiEdgeWeatherResearch/0.3 (NOAA AWC read-only)"})
    assert client is not None
    try:
        response = await client.get(
            AWC_METAR_URL, params={"ids": ",".join(stations), "format": "json"})
        if response.status_code == 204:
            return {"inserted": 0, "rejected": 0, "no_recent_data": True,
                    "mode": "DATA_ONLY"}
        response.raise_for_status()
        receipt = _utc(clock())
        records = response.json()
        if not isinstance(records, list):
            raise ValueError("METAR API response must contain an array")
        observations = []
        rejected = 0
        for item in records[:1000]:
            try:
                point = parse_awc_metar(item, receipt)
                if point["station"] not in stations:
                    rejected += 1
                    continue
                observations.append(point)
            except (ValueError, TypeError, KeyError, OverflowError):
                rejected += 1
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        inserted = 0
        with closing(sqlite3.connect(path, timeout=10)) as conn:
            # The schema is identical to standalone WeatherEdge v0.2.0.
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS samples("
                "id INTEGER PRIMARY KEY,station TEXT,source TEXT,event_time TEXT,"
                "received_at TEXT,payload TEXT)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS samples_pit "
                "ON samples(station,source,received_at,event_time)"
            )
            for point in observations:
                event = point["event_time"].isoformat()
                exists = conn.execute(
                    "SELECT 1 FROM samples WHERE station=? AND source='METAR' "
                    "AND event_time=? LIMIT 1", (point["station"], event)).fetchone()
                if exists:
                    continue
                payload = {"temp_f": point["temp_f"], "raw": point["raw"],
                           "source": "AWC_METAR"}
                conn.execute(
                    "INSERT INTO samples(station,source,event_time,received_at,payload) "
                    "VALUES(?,?,?,?,?)",
                    (point["station"], "METAR", event,
                     point["received_at"].isoformat(),
                     json.dumps(payload, sort_keys=True)),
                )
                inserted += 1
            conn.commit()
        return {"mode": "DATA_ONLY", "inserted": inserted,
                "rejected": rejected, "fetched": len(records),
                "received_at": receipt.isoformat(), "no_recent_data": False}
    finally:
        if own_client:
            await client.aclose()


async def watch_metars(
    db_path: Path, *, stations: tuple[str, ...] = tuple(STATIONS),
    interval_seconds: int = MIN_POLL_SECONDS,
) -> None:
    """Explicit user-run foreground collector. Never a hidden task."""
    if interval_seconds < MIN_POLL_SECONDS:
        raise ValueError("poll interval must be at least five minutes")
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=False,
        headers={"User-Agent": "KalshiEdgeWeatherResearch/0.3 (NOAA AWC read-only)"}
    ) as client:
        while True:
            try:
                report = await collect_metars_once(
                    db_path, client=client, stations=stations)
                print(json.dumps(report, sort_keys=True), flush=True)
            except (httpx.HTTPError, OSError, sqlite3.Error, ValueError) as exc:
                print(json.dumps({
                    "mode": "DATA_ONLY", "status": "UNAVAILABLE",
                    "reason": f"{type(exc).__name__}: {exc}"
                }, sort_keys=True), flush=True)
            await asyncio.sleep(interval_seconds)
