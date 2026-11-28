"""Research-only reconstruction of V12 index nowcast vs V13 on archived Kalshi Index.

Archived event-time observations do not prove original publication/receipt time.
Use a conservative *assumed* 10-minute availability lag, never put reconstructed
points in the live evidence database, and never authorize model promotion.
No trading, orders, or calibration certification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .hourly import API, CITIES
from .hourly_hybrid import fit_hybrid, predict_hybrid
from .hourly_model_comparison import _aggregate, _base_prediction, _empty_metrics, _iso, _pair
from .hourly_nowcast import LEAD_MINUTES, _cases_from_points, choose_index_nowcast

MAX_DAYS = 90
WINDOW = timedelta(days=1)
ASSUMED_PUBLICATION_DELAY_MS = 10 * 60_000
REPLAY_IDENTITY = "RECONSTRUCTED_V12_OBSERVATION_COMPONENT_VS_V13"
REPLAY_RECEIPT = "EVENT_TIME_PLUS_10_MINUTES_UNVERIFIED"
MIN_TRAIN_HOURS = 96
FOLD_HOURS = 24
MAX_RESPONSE_BYTES = 3_000_000
ARCHIVE_CACHE_FORMAT = 1
MAX_RATE_LIMIT_RETRIES = 5
MAX_RETRY_AFTER_SECONDS = 60.0
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.0


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("INVALID_ARCHIVE_TIME")
    return value.astimezone(timezone.utc)


def _empty_report(status: str) -> dict:
    return {
        "status": status, "comparison_identity": REPLAY_IDENTITY,
        "receipt_assumption": REPLAY_RECEIPT,
        "evidence_tier": "HISTORICAL_INDEX_RECONSTRUCTION_UNCERTIFIED",
        "original_publication_times_verified": False,
        "frozen_live_hours": 0, "verified_market_hours": 0,
        "matched_hours": 0, "folds": [],
        "incumbent": _empty_metrics(), "candidate": _empty_metrics(),
        "eligible_for_live_promotion": False,
        "calibration_certified": False, "real_orders_enabled": False,
    }


def parse_archive_calibration_timeline(payload: dict, city: str) -> list[dict]:
    """Validate official effective-date/version mapping; never guess a version.

    This is a source-provenance check, not validation of individual station
    observations or proof of first-publication time.
    """
    if (not isinstance(payload, dict) or payload.get("city") != city or
            payload.get("units") != "celsius"):
        raise ValueError("INVALID_ARCHIVE_CALIBRATION_SOURCE")
    entries = payload.get("calibrations")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("INVALID_ARCHIVE_CALIBRATION_TIMELINE")
    result = []
    previous = -1
    versions = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("INVALID_ARCHIVE_CALIBRATION_ENTRY")
        version = entry.get("config_version")
        effective = entry.get("effective_at_ms")
        published = entry.get("published_at_ms")
        if (not isinstance(version, str) or not 1 <= len(version) <= 160 or
                version in versions or type(effective) is not int or
                not 1500000000000 < effective < 4200000000000 or
                effective <= previous or
                (published is not None and
                 (type(published) is not int or
                  not 1500000000000 < published < 4200000000000))):
            raise ValueError("INVALID_ARCHIVE_CALIBRATION_ENTRY")
        result.append({"config_version": version,
                       "effective_at_ms": effective,
                       "published_at_ms": published})
        previous = effective
        versions.add(version)
    return result


def decode_archive_page(payload: dict, city: str, start_ms: int,
                        end_ms: int, *,
                        calibrations: list[dict] | None = None) -> list[dict]:
    """Decode actual archived observations under each historical configuration.

    Empty windows need no config. For nonempty historical windows use the
    independently published effective-time calibration timeline if supplied.
    Original receipt times are NOT recoverable from a historical response.
    """
    if (city not in CITIES or not isinstance(payload, dict) or
            payload.get("city") != city or payload.get("units") != "fahrenheit" or
            not isinstance(payload.get("timeseries"), list) or
            len(payload["timeseries"]) > 10000):
        raise ValueError("INVALID_ARCHIVE_SOURCE")
    config = payload.get("config_version")
    # Historical/empty windows can omit the optional response-level version,
    # or return an empty string. Neither can identify an old event's config.
    if config == "":
        config = None
    if config is not None and (
            not isinstance(config, str) or not 1 <= len(config) <= 160):
        raise ValueError("INVALID_ARCHIVE_CONFIG")
    if (type(start_ms) is not int or type(end_ms) is not int or
            start_ms < 1600000000000 or end_ms < start_ms):
        raise ValueError("INVALID_ARCHIVE_WINDOW")
    versions = None
    times = None
    if calibrations is not None:
        if not isinstance(calibrations, list) or not calibrations:
            raise ValueError("INVALID_ARCHIVE_CALIBRATION_TIMELINE")
        versions = calibrations
        times = [entry["effective_at_ms"] for entry in versions]
        if times != sorted(set(times)):
            raise ValueError("NONMONOTONIC_ARCHIVE_CALIBRATIONS")
    result = []
    for raw in payload["timeseries"]:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") not in ("normal", "degraded"):
            continue
        if str(raw.get("receipt_basis", "")).lower() == "synthetic":
            continue
        event = raw.get("t")
        value = raw.get("v")
        contributors = raw.get("contributors")
        if (type(event) is not int or not start_ms <= event <= end_ms or
                isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not -130 < value < 160 or
                abs(value-round(value, 2)) > 1e-6 or
                type(contributors) is not int or contributors <= 0):
            continue
        point_config = raw.get("config_version")
        if point_config is not None and (
                not isinstance(point_config, str) or
                not 1 <= len(point_config) <= 160):
            raise ValueError("INVALID_ARCHIVE_CONFIG")
        if times is not None:
            import bisect
            idx = bisect.bisect_right(times, event) - 1
            if idx < 0:
                raise ValueError("NO_APPLICABLE_ARCHIVE_CALIBRATION")
            selected = versions[idx]
            chosen = selected["config_version"]
            published = selected.get("published_at_ms")
            if published is not None and published > event:
                raise ValueError("FUTURE_ARCHIVE_CALIBRATION")
            if point_config is not None and point_config != chosen:
                raise ValueError("ARCHIVE_CALIBRATION_CONFLICT")
        else:
            if config is not None and point_config is not None and point_config != config:
                raise ValueError("INVALID_ARCHIVE_CONFIG")
            chosen = point_config or config
            if chosen is None:
                raise ValueError("INVALID_ARCHIVE_CONFIG")
        result.append({
            "event_ms": event,
            "available_ms": event + ASSUMED_PUBLICATION_DELAY_MS,
            "temp_f": round(value, 2), "status": raw["status"],
            "config_version": chosen,
        })
    return sorted(result, key=lambda p: p["event_ms"])




class _ArchiveRequestPacer:
    """Share one bounded request cadence across cities and calibration calls."""

    def __init__(self, minimum_interval_seconds: float = 0.0):
        if (type(minimum_interval_seconds) not in (int, float) or
                not math.isfinite(minimum_interval_seconds) or
                not 0. <= minimum_interval_seconds <= 60.):
            raise ValueError("INVALID_ARCHIVE_REQUEST_INTERVAL")
        self.interval = float(minimum_interval_seconds)
        self.last_request = None
        self.rate_limited_retries = 0

    def before_request(self) -> None:
        now = time.monotonic()
        if self.last_request is not None:
            remaining = self.interval - (now - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()


def _archive_retry_delay(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    delay = min(32., 2. ** (attempt + 1))
    if raw:
        try:
            delay = float(raw)
        except ValueError:
            try:
                when = parsedate_to_datetime(raw)
                if when.utcoffset() is None:
                    raise ValueError("NAIVE_RETRY_AFTER")
                delay = max(0., (when - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                # An unparseable header is not permission to hammer the API.
                delay = min(32., 2. ** (attempt + 1))
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("INVALID_ARCHIVE_RETRY_AFTER")
    if delay > MAX_RETRY_AFTER_SECONDS:
        # Never retry before the server's explicitly requested delay when
        # that delay exceeds the bounded synchronous request policy.
        raise ValueError("ARCHIVE_RATE_LIMIT_RETRY_AFTER_EXCEEDS_CAP")
    return max(.1, delay)


def _get_archive_source(
    client: httpx.Client, url: str, *,
    params: dict | None = None, pacer: _ArchiveRequestPacer,
) -> bytes:
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        pacer.before_request()
        response = client.get(url, params=params)
        if response.status_code != 429:
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ValueError("OVERSIZED_ARCHIVE_RESPONSE")
            return response.content
        if attempt == MAX_RATE_LIMIT_RETRIES:
            response.raise_for_status()
        delay = _archive_retry_delay(response, attempt)
        pacer.rate_limited_retries += 1
        time.sleep(delay)
    raise ValueError("ARCHIVE_RETRY_EXHAUSTED")


def _cached_window_path(cache_dir: Path, city: str, start_ms: int,
                        end_ms: int) -> Path:
    # The caller has already checked city membership in the whitelist.
    return Path(cache_dir) / city / f"{start_ms}-{end_ms}.json"


def _read_cached_window(path: Path, city: str,
                        start_ms: int, end_ms: int) -> bytes | None:
    if not path.exists():
        return None
    try:
        if path.stat().st_size > (MAX_RESPONSE_BYTES * 2 + 4096):
            raise ValueError("OVERSIZED_CACHE")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or
                saved.get("schema") != ARCHIVE_CACHE_FORMAT or
                saved.get("city") != city or
                saved.get("start_ms") != start_ms or
                saved.get("end_ms") != end_ms or
                saved.get("source") != f"{API}/live_data/weather/{city}"):
            raise ValueError("CACHE_WINDOW_MISMATCH")
        raw = base64.b64decode(saved["body_b64"], validate=True)
        if (len(raw) > MAX_RESPONSE_BYTES or
                hashlib.sha256(raw).hexdigest() != saved["response_sha256"]):
            raise ValueError("CACHE_BODY_HASH_MISMATCH")
        return raw
    except (OSError, UnicodeError, ValueError, TypeError, KeyError,
            base64.binascii.Error) as exc:
        raise ValueError("ARCHIVE_CACHE_INTEGRITY") from exc


def _save_cached_window(path: Path, city: str, start_ms: int,
                        end_ms: int, raw: bytes) -> None:
    # Cache only original, fully decoded and source-checked HTTP 200 data.
    # Atomic writes keep a power loss or interrupted run from creating a
    # seemingly valid partially downloaded day.
    if path.exists():
        existing = _read_cached_window(path, city, start_ms, end_ms)
        if existing != raw:
            raise ValueError("ARCHIVE_CACHE_CONFLICT")
        return
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("OVERSIZED_ARCHIVE_RESPONSE")
    record = {
        "schema": ARCHIVE_CACHE_FORMAT, "city": city,
        "start_ms": start_ms, "end_ms": end_ms,
        "source": f"{API}/live_data/weather/{city}",
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "body_b64": base64.b64encode(raw).decode("ascii"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".weather-archive-",
                suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, sort_keys=True, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_archived_index(
    client: httpx.Client, city: str, start: datetime, end: datetime, *,
    cache_dir: Path | None = None,
    pacer: _ArchiveRequestPacer | None = None,
) -> tuple[list[dict], dict]:
    """Fetch bounded official history with resumable source-validated cache.

    Successful daily response bytes survive interrupted runs; the cache never
    supplies new, unverified observations to the live weather tables.
    """
    start, end = _utc(start), _utc(end)
    if city not in CITIES or not start < end or end > datetime.now(timezone.utc):
        raise ValueError("INVALID_ARCHIVE_RANGE")
    if end-start > timedelta(days=MAX_DAYS):
        raise ValueError("ARCHIVE_RANGE_EXCEEDS_90_DAYS")
    limiter = pacer if pacer is not None else _ArchiveRequestPacer()
    previous_retries = limiter.rate_limited_retries
    records: dict[int, dict] = {}
    receipts = []
    cursor = start
    count = 0
    cache_hits = 0
    calibrations = None
    calibration_sha = None
    while cursor < end:
        stop = min(cursor + WINDOW, end)
        lo = int(cursor.timestamp()*1000)
        hi = int(stop.timestamp()*1000)-1
        cache_path = (_cached_window_path(cache_dir, city, lo, hi)
                      if cache_dir is not None else None)
        raw = (_read_cached_window(cache_path, city, lo, hi)
               if cache_path is not None else None)
        from_cache = raw is not None
        if from_cache:
            cache_hits += 1
        else:
            raw = _get_archive_source(
                client, f"{API}/live_data/weather/{city}",
                params={"from": lo, "to": hi, "detailed": "false"},
                pacer=limiter)
            count += 1
        payload = json.loads(raw)
        # Each nonempty historical window must pass the official effective-
        # time calibration audit. The current response header is not evidence
        # of the version effective on a historical day.
        usable = (isinstance(payload, dict) and
                  isinstance(payload.get("timeseries"), list) and
                  any(isinstance(item, dict) and
                      item.get("status") in ("normal", "degraded")
                      for item in payload["timeseries"]))
        if usable and calibrations is None:
            calibration_bytes = _get_archive_source(
                client, f"{API}/live_data/weather/{city}/calibrations",
                pacer=limiter)
            calibrations = parse_archive_calibration_timeline(
                json.loads(calibration_bytes), city)
            calibration_sha = hashlib.sha256(calibration_bytes).hexdigest()
        page = decode_archive_page(
            payload, city, lo, hi, calibrations=calibrations)
        if cache_path is not None and not from_cache:
            _save_cached_window(cache_path, city, lo, hi, raw)
        receipts.append({
            "window_start_utc": cursor.isoformat(),
            "window_end_utc": stop.isoformat(),
            "source_response_sha256": hashlib.sha256(raw).hexdigest(),
            "decoded_minutes": len(page),
            "cache_hit": from_cache,
        })
        for point in page:
            old = records.get(point["event_ms"])
            if old is not None and old != point:
                raise ValueError("CONFLICTING_ARCHIVED_INDEX")
            records[point["event_ms"]] = point
        cursor = stop
    points = sorted(records.values(), key=lambda p: p["event_ms"])
    return points, {
        "requests": count, "cache_hits": cache_hits,
        "rate_limited_retries": limiter.rate_limited_retries - previous_retries,
        "archived_minutes": len(points),
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "source": f"{API}/live_data/weather/{city}",
        "calibration_source_sha256": calibration_sha,
        "calibration_versions": len(calibrations) if calibrations else 0,
        "response_manifest": receipts,
    }


def archive_index_cases(points: list[dict], end_utc: datetime, *,
                        lead_minutes: int) -> list[dict]:
    """Construct historical examples using strictly pre-forecast observations.

    The 10-minute assumed embargo is only a sensitivity assumption. Versions
    are kept separate so old station calibrations cannot silently blend.
    """
    end_utc = _utc(end_utc)
    if lead_minutes not in LEAD_MINUTES:
        raise ValueError("UNSUPPORTED_ARCHIVE_LEAD")
    grouped: dict[str, list[tuple[int, int, float, str]]] = {}
    seen: set[int] = set()
    for point in sorted(points, key=lambda r: r["event_ms"]):
        ms = point["event_ms"]
        stamp = point["available_ms"]
        temp = point["temp_f"]
        status = point["status"]
        config = point["config_version"]
        if (type(ms) is not int or ms in seen or type(stamp) is not int or
                stamp != ms + ASSUMED_PUBLICATION_DELAY_MS or
                type(temp) not in (float, int) or not math.isfinite(temp) or
                not -130 < temp < 160 or status not in ("normal", "degraded") or
                not isinstance(config, str) or not config or
                ms >= int(end_utc.timestamp()*1000)):
            raise ValueError("INVALID_ARCHIVED_INDEX_POINT")
        seen.add(ms)
        grouped.setdefault(config, []).append((ms, stamp, float(temp), status))
    cases: list[dict] = []
    for config, values in grouped.items():
        values.sort(key=lambda p: p[0])
        for row in _cases_from_points(values, end_utc, lead_minutes):
            cases.append({**row, "source_config_version": config})
    return sorted(cases, key=lambda row: row["target_utc"])


def compare_archived_models(cases: list[dict]) -> dict:
    """Train on earlier reconstructed hours; score disjoint later 24h blocks."""
    report = _empty_report("INSUFFICIENT_ARCHIVED_WALKFORWARD")
    report["available_index_hours"] = len(cases)
    if len(cases) < MIN_TRAIN_HOURS + FOLD_HOURS:
        return report
    sorted_cases = sorted(cases, key=lambda row: _iso(row["target_utc"]))
    stamps = [_iso(row["target_utc"]) for row in sorted_cases]
    if (len(set(stamps)) != len(stamps) or
            any(_iso(row["forecast_asof"]) >= stamp
                for row, stamp in zip(sorted_cases, stamps))):
        report["status"] = "INVALID_ARCHIVE_CASE_CHRONOLOGY"
        return report
    # Keep versions and uninterrupted hourly stretches independent. An Index
    # config change is a fresh training experiment, not a single regime.
    sequences: list[list[dict]] = []
    for row in sorted_cases:
        if (not sequences or
                row["source_config_version"] != sequences[-1][-1]["source_config_version"] or
                (_iso(row["target_utc"]) -
                 _iso(sequences[-1][-1]["target_utc"])) != timedelta(hours=1)):
            sequences.append([row])
        else:
            sequences[-1].append(row)
    all_current = []
    all_candidate = []
    first_errors = []
    for segment in sequences:
        for start in range(MIN_TRAIN_HOURS, len(segment)-FOLD_HOURS+1, FOLD_HOURS):
            holdout = segment[start:start+FOLD_HOURS]
            first_cutoff = _iso(holdout[0]["forecast_asof"])
            cutoff_floor = first_cutoff - timedelta(days=8)
            older = [
                row for row in segment[:start]
                if cutoff_floor <= _iso(row["target_utc"]) and
                _iso(row["label_received_at"]) <= first_cutoff
            ]
            if len(older) < 72:
                continue
            try:
                incumbent = choose_index_nowcast(older)
                params = fit_hybrid(older)
                current = []
                candidate = []
                for row in holdout:
                    if row.get("diurnal_delta_f") is None:
                        raise ValueError("HISTORICAL_DIURNAL_FEATURE_MISSING")
                    base_mean, base_sigma = _base_prediction(row, incumbent)
                    new_mean, new_sigma = predict_hybrid(row, params)
                    current.append(_pair(row, base_mean, base_sigma))
                    candidate.append(_pair(row, new_mean, new_sigma))
            except (KeyError, ValueError, TypeError, OverflowError,
                    ZeroDivisionError, IndexError) as exc:
                first_errors.append(f"{type(exc).__name__}: {exc}"[:100])
                continue
            current_scores = _aggregate(current)
            candidate_scores = _aggregate(candidate)
            report["folds"].append({
                "start_target_utc": holdout[0]["target_utc"],
                "end_target_utc": holdout[-1]["target_utc"],
                "source_config_version": holdout[0]["source_config_version"],
                "train_hours": len(older), "hours": len(holdout),
                "incumbent_model": incumbent.get("model") if incumbent.get("enabled")
                                   else "CURRENT_INDEX_PERSISTENCE",
                "incumbent": current_scores, "candidate": candidate_scores,
            })
            all_current.extend(current)
            all_candidate.extend(candidate)
    report["matched_hours"] = len(all_current)
    report["incumbent"] = _aggregate(all_current)
    report["candidate"] = _aggregate(all_candidate)
    report["skipped_fold_count"] = len(first_errors)
    if first_errors:
        report["first_skipped_fold_reason"] = first_errors[0]
    if all_current:
        report["status"] = "HISTORICAL_RECONSTRUCTION_SCORED"
        report["crps_win_folds"] = sum(
            fold["candidate"]["crps"] < fold["incumbent"]["crps"]
            for fold in report["folds"])
        report["delta_mae_f"] = round(
            report["candidate"]["mae_f"] - report["incumbent"]["mae_f"], 6)
        report["delta_crps"] = round(
            report["candidate"]["crps"] - report["incumbent"]["crps"], 6)
    return report


def run_archive_research(client: httpx.Client, *, cities: tuple[str, ...],
                         start: datetime, end: datetime,
                         leads: tuple[int, ...] = LEAD_MINUTES,
                         cache_dir: Path | None = None,
                         request_interval_seconds: float = 0.0) -> dict:
    start, end = _utc(start), _utc(end)
    if not cities or not leads or any(city not in CITIES for city in cities):
        raise ValueError("INVALID_ARCHIVE_SELECTION")
    if any(lead not in LEAD_MINUTES for lead in leads):
        raise ValueError("INVALID_ARCHIVE_LEADS")
    if not start < end or end > datetime.now(timezone.utc) or end-start > timedelta(days=MAX_DAYS):
        raise ValueError("INVALID_ARCHIVE_RANGE")
    limiter = _ArchiveRequestPacer(request_interval_seconds)
    report = {
        "mode": "WEATHEREDGE_HISTORICAL_REPLAY_V1",
        "status": "NO_ARCHIVED_INDEX_HISTORY",
        "asof_utc": datetime.now(timezone.utc).isoformat(),
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "archive_source": API + "/live_data/weather/{city}",
        "evidence_tier": "HISTORICAL_INDEX_RECONSTRUCTION_UNCERTIFIED",
        "receipt_assumption": REPLAY_RECEIPT,
        "original_publication_times_verified": False,
        "any_promoted": False,
        "calibration_certified": False, "real_orders_enabled": False,
        "groups": [],
    }
    success = 0
    for city in cities:
        try:
            points, provenance = download_archived_index(
                client, city, start, end, cache_dir=cache_dir, pacer=limiter)
        except (httpx.HTTPError, ValueError, KeyError, TypeError,
                OverflowError, json.JSONDecodeError) as exc:
            for lead in leads:
                entry = _empty_report("ARCHIVE_SOURCE_UNAVAILABLE")
                entry.update({
                    "city": city, "lead_minutes": lead,
                    "reason": f"{type(exc).__name__}: {exc}"[:160],
                })
                report["groups"].append(entry)
            continue
        for lead in leads:
            try:
                cases = archive_index_cases(points, end, lead_minutes=lead)
                result = compare_archived_models(cases)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                result = _empty_report("ARCHIVE_SOURCE_AUDIT_ERROR")
                result["reason"] = f"{type(exc).__name__}: {exc}"[:160]
            result.update({
                "city": city, "lead_minutes": lead,
                "archived_minutes": provenance["archived_minutes"],
                "retrieval_requests": provenance["requests"],
                "cache_hits": provenance["cache_hits"],
                "rate_limited_retries": provenance["rate_limited_retries"],
                "archive_calibration_source_sha256": provenance["calibration_source_sha256"],
                "archive_calibration_versions": provenance["calibration_versions"],
                "archive_window": [provenance["start_utc"], provenance["end_utc"]],
                "source_response_sha256": [
                    m["source_response_sha256"] for m in provenance["response_manifest"]],
            })
            report["groups"].append(result)
            success += result["status"] == "HISTORICAL_RECONSTRUCTION_SCORED"
    report["status"] = (
        "HISTORICAL_RECONSTRUCTION_SCORED" if success else
        "ARCHIVE_SOURCE_UNAVAILABLE" if any(
            group["status"] == "ARCHIVE_SOURCE_UNAVAILABLE"
            for group in report["groups"]) else "NO_ARCHIVED_INDEX_HISTORY")
    report["scored_groups"] = success
    report["rate_limited_retries"] = limiter.rate_limited_retries
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Research-only V12 component versus V13 archived-index replay; NO promotion.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--city", action="append", choices=tuple(CITIES))
    parser.add_argument("--lead", action="append", type=int, choices=LEAD_MINUTES)
    parser.add_argument("--end-utc", type=str, help="UTC ISO timestamp, e.g. 2026-11-27T00:00:00Z")
    parser.add_argument("--output", type=Path,
                        help="Save summary JSON here; original archived minute data are cached separately.")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("data/weather/historical_archive_cache_v14"),
                        help="Resumable SHA-256 checked cache of original API response bytes.")
    parser.add_argument("--request-interval-seconds", type=float,
                        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
                        help="Minimum time between requests to the source (default 1 second).")
    args = parser.parse_args()
    if not 1 <= args.days <= MAX_DAYS:
        parser.error("--days must be between 1 and 90")
    if (not math.isfinite(args.request_interval_seconds) or
            not 0 <= args.request_interval_seconds <= 60):
        parser.error("--request-interval-seconds must be between 0 and 60")
    # Midnight UTC produces stable exact 24-hour cache windows between runs.
    end = (_iso(args.end_utc) if args.end_utc else
           datetime.now(timezone.utc).replace(
               hour=0, minute=0, second=0, microsecond=0))
    if end > datetime.now(timezone.utc):
        parser.error("--end-utc cannot be in the future")
    start = end-timedelta(days=args.days)
    cities = tuple(dict.fromkeys(args.city)) if args.city else tuple(CITIES)
    leads = tuple(dict.fromkeys(args.lead)) if args.lead else LEAD_MINUTES
    with httpx.Client(timeout=30., follow_redirects=False,
                      headers={"User-Agent": "KalshiEdge-Weather-Historical-Research/1.0"}) as client:
        report = run_archive_research(
            client, cities=cities, start=start, end=end, leads=leads,
            cache_dir=args.cache_dir,
            request_interval_seconds=args.request_interval_seconds)
    rendered = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["scored_groups"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
