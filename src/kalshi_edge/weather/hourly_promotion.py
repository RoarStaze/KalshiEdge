"""Versioned WEATHER TEMPERATURE promotion from archived walk-forward results.

These are reconstructed Index nowcasts, not original Kalshi market forecasts.
A better reconstructed MAE/CRPS may justify an observation-model upgrade,
but never certifies betting probabilities, settlement, or real orders. Live
coefficients must be refitted from original receipt-safe local observations.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hourly_hybrid import fit_hybrid, predict_hybrid
from .hourly_v14 import fit_v14, predict_v14

V13_FILES = ("historical-backtest-v14.json", "historical-backtest-remaining.json")
V14_FILES = ("historical-backtest-v14-vs-v13.json",)
MAX_REPORT_BYTES = 2_000_000
MAX_REPORT_AGE = timedelta(days=60)
IDENTITIES = {
    "v13": ("WEATHEREDGE_HISTORICAL_REPLAY_V1",
            "RECONSTRUCTED_V12_OBSERVATION_COMPONENT_VS_V13"),
    "v14": ("WEATHEREDGE_HISTORICAL_REPLAY_V14_CHALLENGER",
            "RECONSTRUCTED_V13_VS_V14"),
}
RECEIPT = "EVENT_TIME_PLUS_10_MINUTES_UNVERIFIED"
RESEARCH_TIER = "HISTORICAL_INDEX_RECONSTRUCTION_UNCERTIFIED"


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("INVALID_PROMOTION_TIME")
    return value.astimezone(timezone.utc)


def _valid_number(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value)


def _valid_digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64 and
            all(char in "0123456789abcdef" for char in value))


def _valid_group(group: object, city: str, lead: int,
                 identity: str, start: datetime, end: datetime) -> bool:
    if not isinstance(group, dict):
        return False
    if (group.get("city") != city or group.get("lead_minutes") != lead or
            group.get("comparison_identity") != identity or
            group.get("status") != "HISTORICAL_RECONSTRUCTION_SCORED" or
            group.get("evidence_tier") != RESEARCH_TIER or
            group.get("receipt_assumption") != RECEIPT or
            group.get("original_publication_times_verified") is not False or
            group.get("calibration_certified") is not False or
            group.get("real_orders_enabled") is not False or
            group.get("eligible_for_live_promotion") is not False):
        return False
    try:
        if [_utc(t) for t in group["archive_window"]] != [start, end]:
            return False
        hours = group["matched_hours"]
        if type(hours) is not int or hours < 72:
            return False
        if (type(group["archived_minutes"]) is not int or
                group["archived_minutes"] < 12000 or
                type(group["archive_calibration_versions"]) is not int or
                group["archive_calibration_versions"] < 1 or
                not _valid_digest(group["archive_calibration_source_sha256"])):
            return False
        provenance = group["source_response_sha256"]
        requests = group.get("retrieval_requests", 0)
        cache_hits = group.get("cache_hits", 0)
        if (not isinstance(provenance, list) or not 1 <= len(provenance) <= 90 or
                not all(_valid_digest(d) for d in provenance) or
                type(requests) is not int or type(cache_hits) is not int or
                requests < 0 or cache_hits < 0 or requests + cache_hits != len(provenance)):
            return False
        old, new = group["incumbent"], group["candidate"]
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if (old.get("hours") != hours or new.get("hours") != hours or
                old.get("market_hours") != 0 or new.get("market_hours") != 0):
            return False
        for m in (old, new):
            if (not all(_valid_number(m.get(k))
                        for k in ("mae_f", "crps", "coverage_90")) or
                    not 0 <= m["coverage_90"] <= 1):
                return False
        folds = group["folds"]
        if not isinstance(folds, list) or len(folds) < 3:
            return False
        if (sum(f["hours"] for f in folds) != hours or
                any(f["hours"] != 24 or
                    not _valid_number(f["candidate"]["crps"]) or
                    not _valid_number(f["incumbent"]["crps"])
                    for f in folds)):
            return False
        wins = sum(f["candidate"]["crps"] < f["incumbent"]["crps"]
                   for f in folds)
        return (
            new["mae_f"] < old["mae_f"] and
            new["crps"] < old["crps"] and
            new["coverage_90"] >= .80 and
            new["coverage_90"] >= old["coverage_90"]-.07 and
            wins >= 2)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return False


def _read_valid_winner(path: Path, model: str, city: str,
                       lead: int, asof: datetime) -> dict | None:
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        expected_mode, identity = IDENTITIES[model]
        if (not isinstance(report, dict) or
                report.get("mode") != expected_mode or
                report.get("status") != "HISTORICAL_RECONSTRUCTION_SCORED" or
                report.get("evidence_tier") != RESEARCH_TIER or
                report.get("receipt_assumption") != RECEIPT or
                report.get("real_orders_enabled") is not False or
                report.get("calibration_certified") is not False or
                report.get("original_publication_times_verified") is not False):
            return None
        start, end, computed = (_utc(report[k])
                                for k in ("start_utc", "end_utc", "asof_utc"))
        if (not start < end <= computed <= asof or
                asof-end > MAX_REPORT_AGE or
                end-start > timedelta(days=90)):
            return None
        groups = report["groups"]
        if not isinstance(groups, list) or not 1 <= len(groups) <= 20:
            return None
        matched = [g for g in groups if isinstance(g, dict) and
                   g.get("city") == city and g.get("lead_minutes") == lead]
        if len(matched) != 1 or not _valid_group(matched[0], city, lead, identity, start, end):
            return None
        return matched[0]
    except (OSError, UnicodeError, ValueError, TypeError,
            KeyError, OverflowError, json.JSONDecodeError):
        return None


def read_historical_promotions(db_path: Path, city: str, lead: int, *,
                               asof: datetime | None = None) -> dict:
    """Automatically read the normal research files from the app's data dir.

    No manual promotion command is required. Report-level SHA metadata
    is checked for shape and continuity; original API response bytes are
    NOT reverified by this reader (especially for older uncached runs).
    """
    now = _utc(asof or datetime.now(timezone.utc))
    folder = Path(db_path).parent
    result = {"model": None, "market_promoted": False,
              "evidence_tier": RESEARCH_TIER,
              "status": "NO_QUALIFYING_HISTORICAL_TEMPERATURE_WIN"}
    v13 = next((g for file in V13_FILES
                if (g := _read_valid_winner(folder/file, "v13", city, lead, now)) is not None),
               None)
    if v13 is None:
        return result
    result.update({"model": "v13", "status": "V13_ARCHIVED_TEMPERATURE_WON",
                   "matched_hours": v13["matched_hours"]})
    v14 = next((g for file in V14_FILES
                if (g := _read_valid_winner(folder/file, "v14", city, lead, now)) is not None),
               None)
    if v14 is not None:
        result.update({"model": "v14", "status": "V14_ARCHIVED_TEMPERATURE_WON",
                       "matched_hours": v14["matched_hours"]})
    return result


def promote_local_nowcast(decision: dict, samples: list[dict], *,
                          promoted_version: str, live_delta: float | None) -> dict:
    """Deploy a historical temperature winner on current-regime live data.

    64+ original receipt-safe hourly examples are sufficient to *fit* a
    report-qualified observation challenger. At 96+ hours, the latest 24
    local hours become a separate rollback safety monitor. No archived
    observation is ever inserted in the live receipt evidence database.
    """
    from .hourly_nowcast import (
        HYBRID_MODEL, HOLDOUT, choose_index_nowcast, nowcast_prediction,
        V14_MODEL,
    )
    from .hourly_model_comparison import _aggregate, _pair
    result = dict(decision)
    result["historical_temperature_candidate"] = promoted_version
    if (promoted_version not in ("v13", "v14") or
            not _valid_number(live_delta) or
            len(samples) < 64 or not decision.get("live_slopes")):
        result["historical_temperature_status"] = "WAITING_FOR_LIVE_RECEIPT_SAFE_TRAINING"
        return result
    try:
        ordered = sorted(samples, key=lambda r: _utc(r["target_utc"]))
        local_holdout = len(ordered) >= 96
        if local_holdout:
            train = ordered[:-HOLDOUT]
            valid = ordered[-HOLDOUT:]
            first_asof = _utc(valid[0]["forecast_asof"])
            older = [r for r in train if _utc(r["label_received_at"]) <= first_asof]
            if (len(older) < 64 or
                    any(_utc(valid[i]["target_utc"]) -
                        _utc(valid[i-1]["target_utc"]) != timedelta(hours=1)
                        for i in range(1, len(valid))) or
                    any(r.get("diurnal_delta_f") is None for r in valid)):
                result["historical_temperature_status"] = "WAITING_FOR_UNTOUCHED_LOCAL_HOLDOUT"
                return result
        else:
            # All local source receipts are confirmed at the current live
            # as-of by the original _load_points() source-time checks.
            # The archived 90-day walk-forward is the only held-out
            # comparison until enough local cases accumulate.
            older = ordered
            valid = []

        params = fit_v14(older) if promoted_version == "v14" else fit_hybrid(older)
        predict = predict_v14 if promoted_version == "v14" else predict_hybrid
        if local_holdout:
            # Fit/select the comparison baseline exclusively on the same
            # pre-holdout labels, never using the holdout to choose it.
            original_incumbent = choose_index_nowcast(older)
            current_scores, new_scores = [], []
            for row in valid:
                mean, sigma = nowcast_prediction(
                    row["current_index_f"], row["baseline_sigma_f"],
                    row["fast_slope"], row["slow_slope"], row["hours_ahead"],
                    original_incumbent, diurnal_delta_f=row.get("diurnal_delta_f"))
                new_mean, new_sigma = predict(row, params)
                current_scores.append(_pair(row, mean, sigma))
                new_scores.append(_pair(row, new_mean, new_sigma))
            base, candidate = _aggregate(current_scores), _aggregate(new_scores)
            if (candidate["mae_f"] > base["mae_f"]+.05 or
                    candidate["crps"] > base["crps"]+.025 or
                    candidate["coverage_90"] < max(.75, base["coverage_90"]-.10)):
                result["historical_temperature_status"] = "LOCAL_HOLDOUT_REGRESSION"
                return result
    except (ValueError, TypeError, KeyError, OverflowError,
            ZeroDivisionError, IndexError):
        result["historical_temperature_status"] = "WAITING_FOR_VALID_LOCAL_MODEL_FIT"
        return result
    result.update({
        "enabled": True,
        "model": V14_MODEL if promoted_version == "v14" else HYBRID_MODEL,
        "parameters": params,
        "sigma_scale": (params["v13"] if promoted_version=="v14" else params)["sigma_scale"],
        "live_diurnal_delta_f": live_delta,
        "status": ("ARCHIVED_TEMPERATURE_V14_PROMOTED_UNCERTIFIED"
                   if promoted_version=="v14" else
                   "ARCHIVED_TEMPERATURE_V13_PROMOTED_UNCERTIFIED"),
        "historical_temperature_status": (
            "ACTIVE_OBSERVATION_COMPONENT_ONLY" if local_holdout
            else "ACTIVE_ARCHIVE_EVIDENCE_ONLY"),
        "historical_temperature_validation": {
            "incumbent_mae_f": base["mae_f"] if local_holdout else None,
            "candidate_mae_f": candidate["mae_f"] if local_holdout else None,
            "incumbent_crps": base["crps"] if local_holdout else None,
            "candidate_crps": candidate["crps"] if local_holdout else None,
            "hours": len(valid),
            "market_verified_hours": 0,
        },
    })
    if not local_holdout:
        # Old V12 validation scores must not appear as if they measured
        # this newly selected V13/V14 forecast before a local holdout.
        result["validation"] = None
        result["validation_hours"] = 0
    if local_holdout:
        result["validation_hours"] = len(valid)
        result["validation"] = {
            "baseline_mae_f": base["mae_f"],
            "candidate_mae_f": candidate["mae_f"],
            "baseline_crps": base["crps"],
            "candidate_crps": candidate["crps"],
            "baseline_90_coverage": base["coverage_90"],
            "candidate_90_coverage": candidate["coverage_90"],
        }
    return result
