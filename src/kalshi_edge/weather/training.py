"""Chronological, station-level Gaussian research model using submitted labels.

Training results MUST NOT be called verified settlement or exchange returns.
Input JSONL labels need independent publisher/contract provenance review.
Holdout days are never used to estimate bias or sigma.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .predictor import (
    STATIONS, baseline_mean, _finite, _time, DEFAULT_SIGMA_F,
)
from .calibration import score_gaussian_degree


def _case(raw: dict, station: str) -> dict | None:
    if raw.get("station") != station:
        return None
    asof = _time(raw["asof"])
    day = date.fromisoformat(raw["settlement_day"])
    if abs((day - asof.date()).days) > 1:
        raise ValueError("asof cannot be remote from settlement day")
    entry = {
        "station": station,
        "day": day,
        "asof": asof,
        "hrrr_max_f": _finite(raw["hrrr_max_f"], -130, 150),
        "nbm_max_f": _finite(raw["nbm_max_f"], -130, 150),
        "observed_max_f": _finite(raw["observed_max_f"], -130, 150),
        "trend_f_per_hour": _finite(raw["trend_f_per_hour"], -50, 50),
        "hours_to_peak": _finite(raw["hours_to_peak"], -.001, 25),
        "actual_max_f": _finite(raw["actual_max_f"], -130, 150),
    }
    # Training inputs must record source claims without treating them as proof.
    source = raw.get("label_source")
    if not isinstance(source, str) or not source.strip() or len(source) > 250:
        raise ValueError("missing label source")
    entry["label_source"] = source
    if entry["observed_max_f"] > entry["actual_max_f"] + 20:
        raise ValueError("implausible label/observed high discrepancy")
    return entry


def _raw_prediction(case: dict) -> float:
    return baseline_mean(
        case["hrrr_max_f"], case["nbm_max_f"], case["observed_max_f"],
        case["trend_f_per_hour"], case["hours_to_peak"],
    )


def _score(rows: list[dict], bias: float, sigma: float) -> dict[str, float]:
    """Proper score on fixed -60..140°F outcomes shared by all models.

    The former forecast-centered bins made Brier values incomparable when
    model means differed. Here baseline and challenger use identical classes.
    Full-record MAE retains decimal label precision; daily categorical
    scoring rounds to a whole Fahrenheit degree (settlement-style).
    """
    if not rows:
        raise ValueError("empty scoring split")
    loss = 0.0
    multiclass_brier = 0.0
    mae = 0.0
    for row in rows:
        mean = max(row["observed_max_f"], _raw_prediction(row) + bias)
        actual = row["actual_max_f"]
        outcome = int(math.floor(actual + 0.5))
        score = score_gaussian_degree(mean, sigma, outcome)
        loss += score["log_loss"]
        multiclass_brier += score["multiclass_brier"]
        mae += abs(actual - mean)
    n = len(rows)
    return {
        "log_loss": round(loss / n, 8),
        "multiclass_brier": round(multiclass_brier / n, 8),
        "mae_f": round(mae / n, 6),
    }

def fit_station_model(rows: list[dict], station: str, *, min_days: int = 60) -> dict:
    """Fit only on earlier independent days, score untouched later days."""
    if station not in STATIONS:
        raise ValueError("unknown station")
    if not isinstance(min_days, int) or isinstance(min_days, bool) or min_days < 30:
        raise ValueError("min_days must be >=30")
    cases = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("training records must be JSON objects")
        case = _case(raw, station)
        if case is None:
            continue
        if case["day"] in seen:
            raise ValueError("duplicate independent climate day")
        seen.add(case["day"])
        cases.append(case)
    cases.sort(key=lambda r: r["day"])
    if len(cases) < min_days:
        raise ValueError("minimum independent climate days not met")
    cut = max(1, min(len(cases)-1, int(len(cases) * .7)))
    train = cases[:cut]
    hold = cases[cut:]
    # Fit a 1-parameter bias only to *training* data. The standard deviation
    # is estimated on training residuals, never on held-out observations.
    errors = [r["actual_max_f"] - _raw_prediction(r) for r in train]
    bias = sum(errors) / len(errors)
    bias = max(-24., min(24., bias))
    rmse = math.sqrt(sum(e * e for e in (x-bias for x in errors)) / len(errors))
    sigma = min(29.0, max(.75, rmse))
    trained_score = _score(hold, bias, sigma)
    baseline_score = _score(hold, 0., DEFAULT_SIGMA_F)
    return {
        "schema_version": 1, "model_kind": "STATION_BIAS_GAUSSIAN_RESEARCH",
        "station": station,
        "bias_f": round(bias, 8), "sigma_f": round(sigma, 8),
        "train_days": len(train), "holdout_days": len(hold),
        "train_end_day": train[-1]["day"].isoformat(),
        "holdout_start_day": hold[0]["day"].isoformat(),
        "holdout_end_day": hold[-1]["day"].isoformat(),
        "holdout": {**trained_score,
                    "baseline_log_loss": baseline_score["log_loss"],
                    "baseline_multiclass_brier": baseline_score["multiclass_brier"],
                    "baseline_mae_f": baseline_score["mae_f"]},
        "settlement_verified": False,
        "promoted_to_live": False,
        "provenance": "USER_SUPPLIED_LABELS_NOT_INDEPENDENTLY_CERTIFIED",
        "limitations": (
            "Train and holdout are chronological and split by climate day. "
            "No external audit of label publisher or timestamp provenance. "
            "Gaussian bin probabilities are not guaranteed calibrated or "
            "equivalent to a Kalshi contract's settlement odds."
        ),
    }


def train_model_file(
    jsonl_path: Path, station: str, output_path: Path, *, min_days: int = 60,
) -> dict:
    """Read-only training source, atomic trusted JSON output (never pickle)."""
    source = Path(jsonl_path)
    if not source.is_file() or source.stat().st_size > 15_000_000:
        raise ValueError("training JSONL missing or too large")
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if index > 100_000:
                raise ValueError("too many training records")
            if not line.strip():
                continue
            decoded = json.loads(line)
            if not isinstance(decoded, dict):
                raise ValueError("each line must be a JSON object")
            rows.append(decoded)
    model = fit_station_model(rows, station, min_days=min_days)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".weather_model_", suffix=".tmp", delete=False
        ) as f:
            staging = Path(f.name)
            json.dump(model, f, indent=2, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging, destination)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return model
