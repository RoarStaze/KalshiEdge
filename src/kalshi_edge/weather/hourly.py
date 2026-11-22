"""Official Kalshi city-level hourly temperature index research.

Source: https://docs.kalshi.com/api-reference/live-data/get-weather-index
This is NOT an airport daily-high proxy. The settlement is a city-weighted
minute index near the next top of the hour, subject to each market's actual
rules. Values received at time R cannot be used in a prediction made before R.

Forecasts use an explicitly UNCALIBRATED Gaussian persistence/trend baseline;
they are NOT fair Kalshi market odds. No credentials, order routes or trading.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

BASE_URL = "https://external-api.kalshi.com/trade-api/v2/live_data/weather"
# Exchange index IDs are not city airport codes. Keep their exact case.
CITIES = {
    "chicago": {"name": "Chicago Metro", "series": "KXTEMPCHIHS"},
    "nyc": {"name": "New York City", "series": "KXTEMPNYCHS"},
    "miami": {"name": "Miami", "series": "KXTEMPMIAH"},
    "la-coastal": {"name": "Coastal Los Angeles", "series": "KXTEMPLAXHS"},
}
POLL_SECONDS = 60
MAX_INDEX_STALENESS_SECONDS = 15 * 60
MIN_SIGMA_F = 0.45


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def next_settlement_hour(asof: datetime) -> datetime:
    asof = utc(asof)
    return asof.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _iso_from_ms(value: int) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("index event time must be Unix milliseconds")
    if not 0 < value < 9_000_000_000_000:
        raise ValueError("invalid index event time")
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def parse_index_payload(payload: dict, city: str, received_at: datetime) -> list[dict]:
    """Require the exact official API response shape, not a METAR substitute."""
    receipt = utc(received_at)
    if city not in CITIES or not isinstance(payload, dict):
        raise ValueError("unknown Kalshi index city")
    if payload.get("city") != city or payload.get("units") != "fahrenheit":
        raise ValueError("city or Fahrenheit unit mismatch")
    config = payload.get("config_version")
    if not isinstance(config, str) or len(config) > 160:
        raise ValueError("missing index configuration identifier")
    rows = payload.get("timeseries")
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("unusable index timeseries")
    values = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("invalid index sample")
        status = raw.get("status")
        # An unavailable minute is *not* an observed price or temperature.
        if status == "unavailable":
            continue
        if status not in ("normal", "degraded"):
            raise ValueError("unknown index point status")
        contributors = raw.get("contributors")
        value = raw.get("v")
        if (not isinstance(contributors, int) or isinstance(contributors, bool)
                or contributors < 1 or isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value) or not -130 < value < 150):
            raise ValueError("invalid index temperature or quorum")
        at = _iso_from_ms(raw.get("t"))
        event = datetime.fromisoformat(at)
        if event > receipt + timedelta(seconds=1):
            raise ValueError("future index event time")
        if at in seen:
            raise ValueError("duplicate Kalshi event minute")
        seen.add(at)
        values.append({
            "city": city, "event_at": at,
            "first_received_at": receipt.isoformat(),
            "value_f": float(value), "status": status,
            "contributors": contributors, "config_version": config,
        })
    return values


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS weather_hourly_index("
        "city TEXT NOT NULL,event_at TEXT NOT NULL,"
        "first_received_at TEXT NOT NULL,value_f REAL NOT NULL,"
        "status TEXT NOT NULL,contributors INTEGER NOT NULL,"
        "config_version TEXT NOT NULL,PRIMARY KEY(city,event_at))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS wx_hourly_index_receipts "
        "ON weather_hourly_index(city,first_received_at,event_at)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS weather_hourly_forecasts("
        "city TEXT NOT NULL,target_hour_utc TEXT NOT NULL,"
        "checkpoint_utc TEXT NOT NULL,asof TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,"
        "PRIMARY KEY(city,target_hour_utc,checkpoint_utc))"
    )


async def collect_hourly_indexes(
    db_path: Path, *, client: httpx.AsyncClient | None = None,
    cities: tuple[str, ...] = tuple(CITIES),
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Fetch only first-party, canonically published Kalshi weather indexes.

    First receipt is immutable. A changed value for an existing canonical
    minute is flagged as an integrity conflict, never silently overwritten.
    """
    if not cities or len(set(cities)) != len(cities) or any(c not in CITIES for c in cities):
        raise ValueError("invalid hourly index city selection")
    clock = clock or (lambda: datetime.now(timezone.utc))
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=18, follow_redirects=False,
            headers={"User-Agent": "KalshiEdgeWeatherHourlyResearch/1.0"})
    assert client is not None
    status: dict[str, dict] = {}
    inserted = 0
    try:
        for city in cities:
            try:
                response = await client.get(f"{BASE_URL}/{city}",
                                            params={"last_sec": 900})
                response.raise_for_status()
                if len(response.content) > 1_500_000:
                    raise ValueError("oversized Kalshi index response")
                points = parse_index_payload(response.json(), city, clock())
                if not points:
                    status[city] = {"state": "NO_CANONICAL_POINTS"}
                    continue
                db_path = Path(db_path)
                db_path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(db_path, timeout=5)) as conn:
                    conn.execute("PRAGMA busy_timeout=5000")
                    _ensure_tables(conn)
                    new = 0
                    for item in points:
                        existing = conn.execute(
                            "SELECT value_f,status FROM weather_hourly_index "
                            "WHERE city=? AND event_at=?",
                            (city, item["event_at"]),
                        ).fetchone()
                        if existing is not None:
                            if (abs(float(existing[0])-item["value_f"]) > 0.00001
                                    or existing[1] != item["status"]):
                                raise ValueError("CANONICAL_MINUTE_CHANGED")
                            continue
                        conn.execute(
                            "INSERT INTO weather_hourly_index VALUES(?,?,?,?,?,?,?)",
                            tuple(item[field] for field in (
                                "city", "event_at", "first_received_at", "value_f",
                                "status", "contributors", "config_version",
                            )),
                        )
                        new += 1
                    conn.commit()
                inserted += new
                status[city] = {"state": "READY", "new_points": new,
                                "latest_event_at": max(x["event_at"] for x in points)}
            except (httpx.HTTPError, ValueError, TypeError, KeyError,
                    OverflowError, sqlite3.Error, json.JSONDecodeError) as exc:
                status[city] = {"state": "UNAVAILABLE",
                                "reason": f"{type(exc).__name__}: {exc}"[:220]}
        return {"mode": "PAPER_ONLY", "inserted": inserted, "cities": status,
                "live_orders_enabled": False}
    finally:
        if own:
            await client.aclose()


def _read_points(db_path: Path, city: str, asof: datetime) -> list[tuple]:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
        conn.execute("PRAGMA query_only=ON")
        return conn.execute(
            "SELECT event_at,first_received_at,value_f,config_version FROM "
            "weather_hourly_index WHERE city=? AND event_at<=? "
            "AND first_received_at<=? ORDER BY event_at DESC LIMIT 20",
            (city, asof.isoformat(), asof.isoformat()),
        ).fetchall()


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _empty_city(city: str, state: str, asof: datetime) -> dict:
    return {
        "city": city, "name": CITIES[city]["name"],
        "series_ticker": CITIES[city]["series"],
        "target_hour_utc": next_settlement_hour(asof).isoformat(),
        "asof": asof.isoformat(), "status": state,
        "minutes_remaining": round((next_settlement_hour(asof)-asof).total_seconds()/60, 2),
        "model_kind": "NOT_READY", "last_index_f": None,
        "forecast_f": None, "interval_90_f": None,
        "index_event_at": None, "first_received_at": None,
        "config_version": None, "thresholds": [],
        "settlement_certified": False, "probabilities_calibrated": False,
        "tradable": False, "uncertainty_note": "No validated model coverage",
    }


def read_hourly_forecasts(
    db_path: Path, *, asof: datetime | None = None, preview: bool = False,
) -> dict:
    """Return next-hour index forecasts only; no daily temperature fallback."""
    now = utc(asof or datetime.now(timezone.utc))
    out = {"mode": "PAPER_ONLY", "model": "UNCALIBRATED_INDEX_NOWCAST",
           "live_orders_enabled": False, "hourly_only": True, "forecasts": []}
    for city in CITIES:
        if preview:
            out["forecasts"].append(_empty_city(city, "PREVIEW_NO_DATA", now))
            continue
        if not Path(db_path).is_file():
            out["forecasts"].append(_empty_city(city, "MISSING_INDEX_DATABASE", now))
            continue
        try:
            points = _read_points(db_path, city, now)
        except (sqlite3.Error, OSError, ValueError):
            out["forecasts"].append(_empty_city(city, "INDEX_FEED_UNAVAILABLE", now))
            continue
        if not points:
            out["forecasts"].append(_empty_city(city, "MISSING_INDEX_DATA", now))
            continue
        latest = points[0]
        age = (now - datetime.fromisoformat(latest[0])).total_seconds()
        if age < 0 or age > MAX_INDEX_STALENESS_SECONDS:
            out["forecasts"].append(_empty_city(city, "STALE_INDEX_DATA", now))
            continue
        target = next_settlement_hour(now)
        remaining = (target-now).total_seconds()/60
        # Optional trend uses only points available and received by asof.
        recent = [p for p in points if
                  datetime.fromisoformat(p[0]) >= now-timedelta(minutes=12)]
        slope = 0.
        if len(recent) >= 2:
            a, b = recent[0], recent[-1]
            minutes = (datetime.fromisoformat(a[0])-datetime.fromisoformat(b[0])).total_seconds()/60
            if minutes >= 2:
                slope = max(-0.07, min(0.07, (a[2]-b[2])/minutes))
        # Research heuristic, NOT fitted/calibrated and NOT the market odds.
        # Dampen 5-minute slope to avoid extrapolating a transient spike.
        predicted = float(latest[2]) + slope * min(remaining, 30) * 0.4
        sigma = max(MIN_SIGMA_F, 0.45 + 0.024 * remaining)
        interval = [predicted-1.64485362695*sigma,
                    predicted+1.64485362695*sigma]
        center = int(round(predicted))
        thresholds = [
            {"label": f"{strike}°F or above", "strike_f": strike,
             "probability_yes": round(1-_cdf((strike-predicted)/sigma), 6)}
            for strike in range(center-5, center+6)
        ]
        item = _empty_city(city, "READY_RESEARCH", now)
        item.update({
            "model_kind": "UNCALIBRATED_INDEX_NOWCAST",
            "last_index_f": round(float(latest[2]), 2),
            "forecast_f": round(predicted, 2),
            "interval_90_f": [round(x, 2) for x in interval],
            "index_event_at": latest[0], "first_received_at": latest[1],
            "config_version": latest[3], "thresholds": thresholds,
            "uncertainty_note": "Assumed Gaussian interval; coverage not measured",
        })
        out["forecasts"].append(item)
    return out


def capture_hourly_forecasts(
    db_path: Path, *, asof: datetime | None = None,
) -> dict:
    """First snapshot per UTC five-minute checkpoint, without overwrites."""
    now = utc(asof or datetime.now(timezone.utc))
    report = read_hourly_forecasts(db_path, asof=now)
    ready = [r for r in report["forecasts"] if r["status"] == "READY_RESEARCH"]
    if not ready:
        return {"mode": "PAPER_ONLY", "inserted": 0,
                "status": "WAITING_FOR_OFFICIAL_INDEX"}
    checkpoint = now.replace(minute=(now.minute//5)*5, second=0, microsecond=0)
    inserted = 0
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_tables(conn)
        for item in ready:
            if (datetime.fromisoformat(item["first_received_at"]) > now or
                    datetime.fromisoformat(item["index_event_at"]) > now):
                raise ValueError("future Kalshi index in forecast snapshot")
            payload = json.dumps(item, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            changed = conn.execute(
                "INSERT OR IGNORE INTO weather_hourly_forecasts VALUES(?,?,?,?,?,?)",
                (item["city"], item["target_hour_utc"], checkpoint.isoformat(),
                 now.isoformat(), payload, digest),
            )
            inserted += int(changed.rowcount)
        conn.commit()
    return {"mode": "PAPER_ONLY", "inserted": inserted,
            "status": "RECORDED" if inserted else "EXISTING_CHECKPOINT"}


def hourly_health(db_path: Path, *, preview: bool = False) -> dict:
    """Counts refer only to the hourly index store, never daily samples."""
    result = {"mode": "PAPER_ONLY", "hourly_only": True,
              "hourly_samples": 0, "forecast_snapshots": 0,
              "city_count": 0, "settled_verified": 0,
              "probabilities_calibrated": False,
              "live_orders_enabled": False, "state": "WAITING"}
    if preview or not Path(db_path).is_file():
        result["state"] = "PREVIEW" if preview else "NO_INDEX_DATABASE"
        return result
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            count, cities = conn.execute(
                "SELECT COUNT(*),COUNT(DISTINCT city) FROM weather_hourly_index"
            ).fetchone()
            result["hourly_samples"] = count
            result["city_count"] = cities
            try:
                result["forecast_snapshots"] = conn.execute(
                    "SELECT COUNT(*) FROM weather_hourly_forecasts"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            result["state"] = "COLLECTING" if count else "NO_INDEX_DATA"
    except (sqlite3.Error, OSError):
        result["state"] = "INDEX_STORE_UNAVAILABLE"
    return result
