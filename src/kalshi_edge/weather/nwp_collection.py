"""NOAA HRRR/NBM indexed GRIB2 station collector (optional cfgrib dependency).

Uses official public S3 archives, exact byte-range checks and one atomic DB
commit per full cycle. No Kalshi order endpoint is ever used.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from .predictor import STATIONS, _utc


STATION_COORDINATES = {
    "KORD": (41.9786, -87.9048),
    "KDEN": (39.8561, -104.6737),
    "KATL": (33.6407, -84.4277),
    "KLAX": (33.9416, -118.4085),
}
DEFAULT_HOURS = tuple(range(1, 13))


@dataclass(frozen=True)
class InventoryMessage:
    start: int
    end: int | None
    description: str


def nwp_url(model: str, run: datetime, forecast_hour: int) -> str:
    run = _utc(run)
    if run.minute or run.second or run.microsecond:
        raise ValueError("NOAA model cycle must be an exact UTC hour")
    if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool):
        raise ValueError("forecast hour must be integer")
    if not 0 <= forecast_hour <= 48:
        raise ValueError("forecast hour out of supported range")
    day, hour = run.strftime("%Y%m%d"), run.strftime("%H")
    if model == "HRRR":
        return (f"https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{day}/conus/"
                f"hrrr.t{hour}z.wrfsfcf{forecast_hour:02d}.grib2")
    if model == "NBM" and forecast_hour >= 1:
        return (f"https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.{day}/{hour}/core/"
                f"blend.t{hour}z.core.f{forecast_hour:03d}.co.grib2")
    raise ValueError("unsupported NOAA model/forecast lead")


def parse_index(content: str) -> list[InventoryMessage]:
    raw = []
    for line in content.splitlines():
        match = re.match(r"^\d+:(\d+):", line.strip())
        if match:
            raw.append((int(match.group(1)), line))
    if not raw or len(raw) > 50_000:
        raise ValueError("empty or oversized NOAA GRIB inventory")
    offsets = [offset for offset, _ in raw]
    if any(b <= a for a, b in zip(offsets, offsets[1:])):
        raise ValueError("nonmonotonic GRIB index")
    return [
        InventoryMessage(start, raw[i+1][0] - 1 if i+1 < len(raw) else None, description)
        for i, (start, description) in enumerate(raw)
    ]


def select_2m_temperature(messages: list[InventoryMessage]) -> InventoryMessage:
    # NBM core inventories contain multiple TMP/2m products, notably the
    # ordinary temperature forecast AND its ensemble standard deviation.
    # The former is usable as Kelvin guidance; the latter is a spread in
    # Kelvin and MUST NOT be treated as air temperature. Require the normal
    # forecast field to end after the lead-time token (trailing colon optional).
    # This also rejects percentile/probability variants and keeps a strict
    # ambiguity failure if NOAA ever publishes two ordinary candidates.
    matching = [
        item for item in messages
        if re.search(
            r":TMP:2 m above ground:[^:]+:?\s*$",
            item.description,
            flags=re.IGNORECASE,
        )
    ]
    if len(matching) != 1:
        raise ValueError("missing or ambiguous ordinary NOAA 2 m temperature field")
    return matching[0]


async def fetch_grib_temperature(client: httpx.AsyncClient, url: str) -> bytes:
    index = await client.get(url + ".idx")
    index.raise_for_status()
    if len(index.content) > 8_000_000:
        raise ValueError("oversized GRIB inventory")
    item = select_2m_temperature(parse_index(index.text))
    end = item.end
    if end is None:
        response = await client.head(url)
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if not length or not length.isdecimal():
            raise ValueError("missing NOAA GRIB Content-Length")
        end = int(length) - 1
    if not item.start <= end or end - item.start > 100_000_000:
        raise ValueError("unexpected NOAA GRIB field size")
    response = await client.get(
        url, headers={"Range": f"bytes={item.start}-{end}"})
    if response.status_code != 206:
        raise ValueError("NOAA GRIB endpoint did not return HTTP 206")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)",
                         response.headers.get("Content-Range", ""))
    if match is None or int(match[1]) != item.start or int(match[2]) != end:
        raise ValueError("GRIB Content-Range mismatch")
    if len(response.content) != end - item.start + 1 or not response.content.startswith(b"GRIB"):
        raise ValueError("truncated or invalid NOAA GRIB message")
    return response.content


def decode_station_temperatures(grib_message: bytes,
                                stations: dict[str, tuple[float, float]]) -> dict[str, float]:
    """Decode one 2m grid field and sample nearest point for each station.

    Requires optional xarray/cfgrib/eccodes/numpy packages. Grid values are
    *numerical guidance*, not authoritative airport observations.
    """
    import numpy as np
    import xarray as xr
    with tempfile.TemporaryDirectory() as directory:
        location = Path(directory) / "field.grib2"
        location.write_bytes(grib_message)
        ds = xr.open_dataset(location, engine="cfgrib", backend_kwargs={"indexpath": ""})
        try:
            variable = next((v for v in ("t2m", "t", "TMP") if v in ds.data_vars), None)
            if variable is None:
                raise ValueError("decoded field does not contain 2m temperature")
            lat, lon = ds["latitude"], ds["longitude"]
            values = {}
            for station, (latitude, longitude) in stations.items():
                # Nearest-grid longitude uses circular angular difference.
                distance = (lat - latitude) ** 2 + (((lon - longitude + 180) % 360) - 180) ** 2
                coordinate = np.unravel_index(int(np.nanargmin(distance.values)), distance.shape)
                value = float(ds[variable].isel({
                    dim: i for dim, i in zip(distance.dims, coordinate)
                }).values.item())
                if not math.isfinite(value) or not 150 < value < 350:
                    raise ValueError("invalid GRIB Kelvin value")
                values[station] = value
            return values
        finally:
            ds.close()


async def collect_cycle(
    client: httpx.AsyncClient, db_path: Path, model: str, run: datetime,
    forecast_hours: list[int] | tuple[int, ...] = DEFAULT_HOURS,
    *, stations: dict[str, tuple[float, float]] | None = None,
    decode: Callable[[bytes, dict[str, tuple[float, float]]], dict[str, float]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Acquire *all* forecast-hour data before recording availability.

    No partial model cycle is inserted if any forecast-hour decode fails.
    Every stored receipt timestamp is no earlier than the final successful
    decoding step.
    """
    stations = stations or STATION_COORDINATES
    if not stations or any(s not in STATIONS for s in stations):
        raise ValueError("unknown airport in collection request")
    if not forecast_hours or len(set(forecast_hours)) != len(forecast_hours):
        raise ValueError("forecast hours missing or duplicated")
    if model not in ("HRRR", "NBM"):
        raise ValueError("unsupported NOAA model")
    run = _utc(run)
    decoded = decode or decode_station_temperatures
    clock = clock or (lambda: datetime.now(timezone.utc))
    fetched = []
    for hour in sorted(forecast_hours):
        url = nwp_url(model, run, hour)
        data = await fetch_grib_temperature(client, url)
        points = await asyncio.to_thread(decoded, data, stations)
        if set(points) != set(stations):
            raise ValueError("GRIB station decode did not return all configured stations")
        for station, kelvin in points.items():
            if not math.isfinite(kelvin) or not 150 < kelvin < 350:
                raise ValueError("nonfinite or implausible station Kelvin")
        fetched.append((hour, url, hashlib.sha256(data).hexdigest(), points))
    received = _utc(clock())
    if received < run:
        raise ValueError("receipt predates NOAA cycle")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    inserted = 0
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS samples("
            "id INTEGER PRIMARY KEY,station TEXT,source TEXT,event_time TEXT,"
            "received_at TEXT,payload TEXT)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS samples_pit "
                     "ON samples(station,source,received_at,event_time)")
        # No fake receipt times: all rows become available together after ingest.
        for hour, url, digest, points in fetched:
            for station, value in points.items():
                payload = {
                    "station": station, "source": model,
                    "temperature_k": value, "model_run_at": run.isoformat(),
                    "forecast_hour": hour,
                    "forecast_valid_at": (run + timedelta(hours=hour)).isoformat(),
                    "received_at": received.isoformat(),
                    "grib_sha256": digest, "data_url": url,
                }
                previous = conn.execute(
                    "SELECT 1 FROM samples WHERE station=? AND source=? "
                    "AND event_time=? AND received_at=? AND payload=? LIMIT 1",
                    (station, model, run.isoformat(), received.isoformat(),
                     json.dumps(payload, sort_keys=True)),
                ).fetchone()
                if previous:
                    continue
                conn.execute(
                    "INSERT INTO samples(station,source,event_time,received_at,payload)"
                    " VALUES(?,?,?,?,?)",
                    (station, model, run.isoformat(), received.isoformat(),
                     json.dumps(payload, sort_keys=True)),
                )
                inserted += 1
        conn.commit()
    return {
        "model": model, "cycle": run.isoformat(), "received_at": received.isoformat(),
        "points": inserted, "stations": list(stations),
        "mode": "DATA_ONLY", "source": "NOAA_PUBLIC_GRIB_NOT_SETTLEMENT",
    }


async def collect_recent(
    db_path: Path, *, models: tuple[str, ...] = ("HRRR", "NBM"),
    forecast_hours: list[int] | tuple[int, ...] = DEFAULT_HOURS,
    lookback_hours: int = 5,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Prefer newest *complete* NOAA cycle; report errors instead of fabricating."""
    if not 1 <= lookback_hours <= 24:
        raise ValueError("invalid NWP lookback")
    if any(m not in ("HRRR", "NBM") for m in models):
        raise ValueError("unsupported model")
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=90, follow_redirects=False,
            headers={"User-Agent": "KalshiEdgeWeatherResearch/0.3 (NOAA public data)"})
    assert client is not None
    try:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        result = {}
        for model in models:
            latest_error = None
            for hours_ago in range(1, lookback_hours + 1):
                try:
                    result[model] = await collect_cycle(
                        client, db_path, model, now - timedelta(hours=hours_ago),
                        forecast_hours)
                    break
                except (httpx.HTTPError, ValueError, OSError, ImportError,
                        KeyError, sqlite3.Error) as exc:
                    latest_error = f"{type(exc).__name__}: {exc}"
            else:
                result[model] = {"status": "UNAVAILABLE", "reason": latest_error}
        return result
    finally:
        if own:
            await client.aclose()


async def watch_nwp(db_path: Path, *, interval_seconds: int = 3600) -> None:
    if interval_seconds < 900:
        raise ValueError("NWP poll interval must be at least 15 minutes")
    while True:
        try:
            report = await collect_recent(db_path)
            print(json.dumps(report, sort_keys=True), flush=True)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            print(json.dumps({"status": "UNAVAILABLE", "reason": str(exc)},
                             sort_keys=True), flush=True)
        await asyncio.sleep(interval_seconds)
