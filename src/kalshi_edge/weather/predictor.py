"""Conservative point-in-time station temperature probability engine.

This is an *experimental airport proxy*, not a certified Kalshi settlement
forecast. Uses actual receipt times and an intact NWP run per model.
No orders, credentials, pickle, network requests, or training side effects.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .status import weather_database_path


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    standard_offset_hours: int
    expected_peak_hour_standard: int = 15
    # Only market series explicitly evidenced for this airport. A forecast
    # against KORD must NEVER be labeled as Chicago KXHIGHCHI (CLIMDW).
    kalshi_series: str | None = None
    settlement_station: str | None = None


STATIONS: dict[str, Station] = {
    "KMDW": Station("KMDW", "Chicago Midway · Kalshi Chicago", -6,
                     kalshi_series="KXHIGHCHI", settlement_station="CLIMDW"),
    "KORD": Station("KORD", "Chicago O'Hare · separate research", -6),
    "KDEN": Station("KDEN", "Denver International", -7,
                     kalshi_series="KXHIGHDEN", settlement_station="CLIDEN"),
    "KATL": Station("KATL", "Atlanta Hartsfield-Jackson", -5,
                     kalshi_series="KXHIGHTATL", settlement_station="CLIATL"),
    "KLAX": Station("KLAX", "Los Angeles International", -8,
                     kalshi_series="KXHIGHLAX", settlement_station="CLILAX"),
}

NWP_MAX_AGE = timedelta(hours=5)
METAR_MAX_AGE = timedelta(minutes=90)
DEFAULT_SIGMA_F = 3.0  # HEURISTIC: not calibrated until labels/holdout exist


def _utc(t: datetime) -> datetime:
    if not isinstance(t, datetime) or t.utcoffset() is None:
        raise ValueError("asof must have timezone information")
    return t.astimezone(timezone.utc)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _finite(value: object, lower: float, upper: float) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a temperature")
    n = float(value)
    if not math.isfinite(n) or not lower < n < upper:
        raise ValueError("out of range temperature")
    return n


def station_day(spec: Station, asof: datetime) -> tuple[date, datetime, datetime, datetime]:
    """Climate-day assumption: 00:00-23:59 *local standard time*.

    This proxy is not a statement about any particular Kalshi contract.
    """
    offset = timedelta(hours=spec.standard_offset_hours)
    local = _utc(asof) + offset
    start = datetime(local.year, local.month, local.day, tzinfo=timezone.utc) - offset
    peak = start + timedelta(hours=spec.expected_peak_hour_standard)
    return local.date(), start, start + timedelta(days=1), peak


def normal_cdf(x: float, mean: float, sigma: float) -> float:
    if not all(math.isfinite(v) for v in (x, mean, sigma)) or sigma <= 0:
        raise ValueError("invalid probability parameters")
    return min(1.0, max(0.0, (1 + math.erf((x - mean) / (sigma * math.sqrt(2)))) / 2))


def normal_bucket_probabilities(mean: float, sigma: float) -> list[dict]:
    """One-degree Fahrenheit rounded airport *research* bins plus both tails.

    These are not assumed to be Kalshi's contract-specific outcome brackets.
    """
    if not all(math.isfinite(v) for v in (mean, sigma)) or not 0 < sigma <= 30:
        raise ValueError("invalid temperature distribution")
    center = round(mean)
    lower, upper = center - 10, center + 10
    entries = [{
        "label": f"≤ {lower-1} °F", "lower_f": None, "upper_f": lower - 1,
        "probability": normal_cdf(lower - .5, mean, sigma),
    }]
    for degree in range(lower, upper + 1):
        entries.append({
            "label": f"{degree} °F", "lower_f": degree, "upper_f": degree,
            "probability": max(0., normal_cdf(degree+.5, mean, sigma)
                                  - normal_cdf(degree-.5, mean, sigma)),
        })
    entries.append({
        "label": f"≥ {upper+1} °F", "lower_f": upper+1, "upper_f": None,
        "probability": 1 - normal_cdf(upper + .5, mean, sigma),
    })
    # Sum differs from one only by floating point rounding.
    total = sum(item["probability"] for item in entries)
    if total <= 0:
        raise ValueError("invalid probability mass")
    for item in entries:
        item["probability"] /= total
    return entries


def baseline_mean(hrrr: float, nbm: float, observed_max: float,
                  trend_f_per_hour: float, hours_to_peak: float) -> float:
    """Inspectable research heuristic; not a calibrated station model."""
    signal = .5 * (hrrr + nbm)
    adjustment = max(-4., min(4., trend_f_per_hour)) * min(hours_to_peak, 3.) * .12
    return max(observed_max, signal + adjustment)


def _rows(conn: sqlite3.Connection, station: str, source: str,
          from_time: datetime, asof: datetime) -> list[tuple[datetime, datetime, dict]]:
    # Store and collector use UTC datetime.isoformat() (including +00:00).
    # Verify again in Python to avoid accepting malformed or non-UTC entries.
    results = []
    query = ("SELECT event_time,received_at,payload FROM samples "
             "WHERE station=? AND UPPER(source)=? "
             "AND event_time>=? AND event_time<=? AND received_at<=? "
             "ORDER BY event_time DESC,received_at DESC,id DESC LIMIT 4000")
    for event_raw, received_raw, payload_raw in conn.execute(
            query, (station, source, from_time.isoformat(), asof.isoformat(), asof.isoformat())):
        try:
            event, received = _time(event_raw), _time(received_raw)
            if not from_time <= event <= asof or received > asof or received < event:
                continue
            payload = json.loads(payload_raw)
            if not isinstance(payload, dict):
                continue
            results.append((event, received, payload))
        except (ValueError, TypeError, json.JSONDecodeError, OverflowError):
            continue
    return results


def _metars(rows: list[tuple[datetime, datetime, dict]], asof: datetime) -> tuple[float, float, float, datetime, datetime]:
    by_event = {}
    for event, received, payload in rows:
        try:
            value = _finite(payload["temp_f"], -130, 150)
        except (ValueError, TypeError, KeyError):
            continue
        old = by_event.get(event)
        if old is None or received > old[0]:
            by_event[event] = (received, value)
    if not by_event:
        raise LookupError("MISSING_METAR")
    ordered = sorted(by_event.items())
    most_recent_event = ordered[-1][0]
    if asof - most_recent_event > METAR_MAX_AGE:
        raise LookupError("STALE_METAR")
    latest = ordered[-1][1][1]
    prior = next(((t, item[1]) for t, item in reversed(ordered[:-1]) if t < most_recent_event), None)
    if prior:
        hours = (most_recent_event - prior[0]).total_seconds() / 3600
        trend = (latest - prior[1]) / hours if hours > 0 else 0.
    else:
        trend = 0.
    return (max(x[1][1] for x in ordered), latest, trend,
            most_recent_event, ordered[-1][1][0])


def _model_day_max(
    source: str, rows: list[tuple[datetime, datetime, dict]],
    day_start: datetime, day_end: datetime, peak: datetime, asof: datetime,
) -> tuple[float, str, str]:
    cycles: dict[datetime, dict[datetime, tuple[datetime, float]]] = {}
    for event, received, payload in rows:
        try:
            cycle = _time(payload["model_run_at"])
            valid = _time(payload["forecast_valid_at"])
            kelvin = _finite(payload["temperature_k"], 150, 350)
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if not (day_start <= valid < day_end) or cycle != event:
            continue
        if cycle > asof or received > asof or received < cycle:
            continue
        previous = cycles.setdefault(cycle, {}).get(valid)
        if previous is None or received > previous[0]:
            cycles[cycle][valid] = (received, kelvin)
    for cycle in sorted(cycles, reverse=True):
        age = asof - cycle
        if not timedelta() <= age <= NWP_MAX_AGE:
            continue
        points = cycles[cycle]
        # Demand at least three forecasts and coverage of afternoon maximum.
        if len(points) < 3 or not any(abs(v - peak) <= timedelta(hours=1) for v in points):
            continue
        value = max(point[1] for point in points.values())
        return ((value - 273.15) * 9 / 5 + 32, cycle.isoformat(),
                max(point[0] for point in points.values()).isoformat())
    if cycles and any(asof - x > NWP_MAX_AGE for x in cycles):
        raise LookupError("STALE_" + source)
    raise LookupError("INCOMPLETE_" + source)


def _load_model(path: Path, station: str, asof_day: date) -> tuple[float, float, str, int]:
    if not path.is_file():
        return 0., DEFAULT_SIGMA_F, "BASELINE_UNCALIBRATED", 0
    try:
        if path.stat().st_size > 200_000:
            raise ValueError("oversized model")
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or obj.get("station") != station:
            raise ValueError("model station mismatch")
        if obj.get("model_kind") != "STATION_BIAS_GAUSSIAN_RESEARCH":
            raise ValueError("unsupported model kind")
        bias = _finite(obj["bias_f"], -25, 25)
        sigma = _finite(obj["sigma_f"], .1, 30)
        n = int(obj["train_days"])
        if isinstance(obj["train_days"], bool) or n < 30:
            raise ValueError("insufficient training days")
        if date.fromisoformat(obj["train_end_day"]) >= asof_day:
            raise ValueError("model trained on future settlement")
        return bias, sigma, "STATION_BIAS_GAUSSIAN_RESEARCH", n
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError("invalid model artifact") from exc


def read_station_forecast(
    db_path: Path, station: str, asof: datetime | None = None,
    *, model_dir: Path | None = None, baseline_only: bool = False,
) -> dict:
    """Produce station airport-proxy distribution only from data known at asof.

    A number is a *research prediction*, NOT a Kalshi-tradable probability.
    """
    if station not in STATIONS:
        raise ValueError("unrecognized airport station")
    asof = _utc(asof or datetime.now(timezone.utc))
    spec = STATIONS[station]
    climate_day, start, end, peak = station_day(spec, asof)
    result: dict[str, Any] = {
        "station": station, "name": spec.name, "climate_day": climate_day.isoformat(),
        "asof": asof.isoformat(), "status": "MISSING_DATABASE",
        "mode": "PAPER_ONLY", "tradable": False, "settlement_verified": False,
        "model_kind": None, "mean_f": None, "sigma_f": None,
        "hrrr_max_f": None, "nbm_max_f": None, "observed_max_f": None,
        "current_f": None, "hours_to_peak": None, "trend_f_per_hour": None,
        "interval_90_f": None, "buckets": [], "n_training_days": 0,
        "metar_event_at": None, "metar_received_at": None,
        "nwp_receipts_utc": {}, "nwp_cycles_utc": {},
        "source_statuses": {
            "METAR": "NOT_CHECKED", "HRRR": "NOT_CHECKED", "NBM": "NOT_CHECKED",
        },
    }
    if not Path(db_path).is_file():
        return result
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=.5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            metars = _rows(conn, station, "METAR", start, asof)
            try:
                observed, current, trend, metar_event, metar_receipt = _metars(metars, asof)
            except LookupError as exc:
                result["source_statuses"]["METAR"] = str(exc)
                raise
            result["source_statuses"]["METAR"] = "READY"
            result["observed_max_f"] = round(observed, 3)
            result["current_f"] = round(current, 3)
            result["metar_event_at"] = metar_event.isoformat()
            result["metar_received_at"] = metar_receipt.isoformat()
            result["trend_f_per_hour"] = round(trend, 3)
            result["hours_to_peak"] = round(
                max(0., (peak - asof).total_seconds() / 3600), 3)
            # Diagnose each model independently. A usable HRRR forecast must
            # remain visible when NBM is late, missing or undecodable.
            # Never combine available slices from two different model cycles.
            nwp_maxes = {}
            missing = []
            for model in ("HRRR", "NBM"):
                try:
                    temperature, cycle, receipt = _model_day_max(
                        model,
                        _rows(conn, station, model, asof - NWP_MAX_AGE, asof),
                        start, end, peak, asof,
                    )
                except LookupError as exc:
                    result["source_statuses"][model] = str(exc)
                    missing.append(str(exc))
                else:
                    result["source_statuses"][model] = "READY"
                    result[model.lower() + "_max_f"] = round(temperature, 3)
                    result["nwp_cycles_utc"][model] = cycle
                    result["nwp_receipts_utc"][model] = receipt
                    nwp_maxes[model] = temperature
            if missing:
                # Preserve every available model/observation field while
                # leaving probability distribution unpopulated (fail closed).
                raise LookupError(missing[0])
            hrrr, nbm = nwp_maxes["HRRR"], nwp_maxes["NBM"]
        hours = max(0., (peak - asof).total_seconds() / 3600)
        if baseline_only:
            bias, sigma, model_kind, n_days = (
                0., DEFAULT_SIGMA_F, "BASELINE_UNCALIBRATED", 0)
        else:
            bias, sigma, model_kind, n_days = _load_model(
                (model_dir or Path(db_path).parent / "models") / f"{station}.json",
                station, climate_day)
        base = baseline_mean(hrrr, nbm, observed, trend, hours)
        mean = max(observed, base + bias)
        result.update({
            "status": "READY_RESEARCH", "model_kind": model_kind,
            "mean_f": round(mean, 3), "sigma_f": round(sigma, 3),
            "interval_90_f": [round(mean-1.64485362695*sigma, 2),
                              round(mean+1.64485362695*sigma, 2)],
            "buckets": normal_bucket_probabilities(mean, sigma),
            "n_training_days": n_days,
        })
    except LookupError as exc:
        result["status"] = str(exc)
    except ValueError as exc:
        # Invalid model metadata cannot silently fall back to the baseline.
        result["status"] = "INVALID_MODEL" if str(exc) == "invalid model artifact" else "INVALID_INPUT"
    except (sqlite3.Error, OSError):
        result["status"] = "DATABASE_ERROR"
    return result


def read_weather_forecasts(root: Path, *, asof: datetime | None = None,
                           preview: bool = False) -> dict:
    """Safe fixed-path dashboard API; preview never touches the local DB."""
    result = {
        "mode": "PAPER_ONLY", "real_orders_enabled": False,
        "settlement_verification": "NOT_CERTIFIED",
        "distribution_type": "AIRPORT_PROXY_EXPERIMENTAL_NOT_CONTRACT_BINS",
        "forecasts": [],
    }
    db = weather_database_path(root)
    for station in STATIONS:
        if preview:
            item = read_station_forecast(Path("/__unavailable_weatheredge_preview__"),
                                         station, asof)
            item["status"] = "PREVIEW_NO_DATA"
        else:
            item = read_station_forecast(db, station, asof)
        result["forecasts"].append(item)
    return result
