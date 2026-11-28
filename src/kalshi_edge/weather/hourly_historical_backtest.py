"""Research-only reconstruction of V12 index nowcast vs V13 on archived Kalshi Index.

Archived event-time observations do not prove original publication/receipt time.
Use a conservative *assumed* 10-minute availability lag, never put reconstructed
points in the live evidence database, and never authorize model promotion.
No trading, orders, or calibration certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

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


def decode_archive_page(payload: dict, city: str, start_ms: int,
                        end_ms: int) -> list[dict]:
    """Validate separately queried *historical* exchange observations.

    available_ms is a simulated embargo, NOT a claimed original receipt time.
    Archive responses must not be copied into weather_hourly_index.
    """
    if (city not in CITIES or not isinstance(payload, dict) or
            payload.get("city") != city or payload.get("units") != "fahrenheit" or
            not isinstance(payload.get("timeseries"), list) or
            len(payload["timeseries"]) > 10000):
        raise ValueError("INVALID_ARCHIVE_SOURCE")
    config = payload.get("config_version")
    if not isinstance(config, str) or not config or len(config) > 160:
        raise ValueError("INVALID_ARCHIVE_CONFIG")
    if (type(start_ms) is not int or type(end_ms) is not int or
            start_ms < 1600000000000 or end_ms < start_ms):
        raise ValueError("INVALID_ARCHIVE_WINDOW")
    result = []
    for raw in payload["timeseries"]:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") not in ("normal", "degraded"):
            continue
        if str(raw.get("receipt_basis", "")).lower() == "synthetic":
            continue
        if raw.get("config_version", config) != config:
            raise ValueError("INVALID_ARCHIVE_CONFIG")
        event = raw.get("t")
        value = raw.get("v")
        contributors = raw.get("contributors")
        if (type(event) is not int or not start_ms <= event <= end_ms or
                isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not -130 < value < 160 or
                abs(value-round(value, 2)) > 1e-6 or
                type(contributors) is not int or contributors <= 0):
            continue
        result.append({
            "event_ms": event,
            "available_ms": event + ASSUMED_PUBLICATION_DELAY_MS,
            "temp_f": round(value, 2), "status": raw["status"],
            "config_version": config,
        })
    return sorted(result, key=lambda p: p["event_ms"])


def download_archived_index(client: httpx.Client, city: str,
                            start: datetime, end: datetime) -> tuple[list[dict], dict]:
    """Fetch bounded official history in 24h chunks; no future or proxy data."""
    start, end = _utc(start), _utc(end)
    if city not in CITIES or not start < end or end > datetime.now(timezone.utc):
        raise ValueError("INVALID_ARCHIVE_RANGE")
    if end-start > timedelta(days=MAX_DAYS):
        raise ValueError("ARCHIVE_RANGE_EXCEEDS_90_DAYS")
    records: dict[int, dict] = {}
    receipts = []
    cursor = start
    count = 0
    while cursor < end:
        stop = min(cursor + WINDOW, end)
        lo = int(cursor.timestamp()*1000)
        hi = int(stop.timestamp()*1000)-1
        response = client.get(f"{API}/live_data/weather/{city}",
                              params={"from": lo, "to": hi, "detailed": "false"})
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ValueError("OVERSIZED_ARCHIVE_RESPONSE")
        page = decode_archive_page(response.json(), city, lo, hi)
        count += 1
        receipts.append({
            "window_start_utc": cursor.isoformat(),
            "window_end_utc": stop.isoformat(),
            "source_response_sha256": hashlib.sha256(response.content).hexdigest(),
            "decoded_minutes": len(page),
        })
        for point in page:
            old = records.get(point["event_ms"])
            if old is not None and old != point:
                raise ValueError("CONFLICTING_ARCHIVED_INDEX")
            records[point["event_ms"]] = point
        cursor = stop
    points = sorted(records.values(), key=lambda p: p["event_ms"])
    return points, {
        "requests": count, "archived_minutes": len(points),
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "source": f"{API}/live_data/weather/{city}",
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
                         leads: tuple[int, ...] = LEAD_MINUTES) -> dict:
    start, end = _utc(start), _utc(end)
    if not cities or not leads or any(city not in CITIES for city in cities):
        raise ValueError("INVALID_ARCHIVE_SELECTION")
    if any(lead not in LEAD_MINUTES for lead in leads):
        raise ValueError("INVALID_ARCHIVE_LEADS")
    if not start < end or end > datetime.now(timezone.utc) or end-start > timedelta(days=MAX_DAYS):
        raise ValueError("INVALID_ARCHIVE_RANGE")
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
            points, provenance = download_archived_index(client, city, start, end)
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
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Research-only V12 component versus V13 archived-index replay; NO promotion.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--city", action="append", choices=tuple(CITIES))
    parser.add_argument("--lead", action="append", type=int, choices=LEAD_MINUTES)
    parser.add_argument("--end-utc", type=str, help="UTC ISO timestamp, e.g. 2026-11-27T00:00:00Z")
    parser.add_argument("--output", type=Path,
                        help="Save summary JSON here; original archived minute data are not saved.")
    args = parser.parse_args()
    if not 1 <= args.days <= MAX_DAYS:
        parser.error("--days must be between 1 and 90")
    end = (_iso(args.end_utc) if args.end_utc else
           datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0))
    if end > datetime.now(timezone.utc):
        parser.error("--end-utc cannot be in the future")
    start = end-timedelta(days=args.days)
    cities = tuple(dict.fromkeys(args.city)) if args.city else tuple(CITIES)
    leads = tuple(dict.fromkeys(args.lead)) if args.lead else LEAD_MINUTES
    with httpx.Client(timeout=30., follow_redirects=False,
                      headers={"User-Agent": "KalshiEdge-Weather-Historical-Research/1.0"}) as client:
        report = run_archive_research(
            client, cities=cities, start=start, end=end, leads=leads)
    rendered = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["scored_groups"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
