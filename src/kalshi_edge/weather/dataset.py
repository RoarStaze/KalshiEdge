"""Retrospective feature/label export from first-party publisher evidence references.

This joins actual receipt-time-limited weather observations to manually
documented publisher outcomes. A URL and hash are *not authentication*:
exports always state that independent settlement review is still required.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from .predictor import (
    STATIONS, _finite, _time, read_station_forecast, station_day,
)

_ALLOWED_PUBLISHERS = (
    "https://weather.com/kalshi",
    "https://api.weather.gov/products/",
)
_TICKER = re.compile(r"^[A-Z0-9-]{4,100}$")
_HEX256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _validate_manifest_row(raw: dict, station: str) -> dict | None:
    if raw.get("station") != station:
        return None
    try:
        day = date.fromisoformat(raw["settlement_day"])
        asof = _time(raw["asof"])
        actual = _finite(raw["actual_max_f"], -130, 150)
        uri = raw["source_uri"]
        digest = raw["source_sha256"]
        ticker = raw["source_market_ticker"]
    except KeyError as exc:
        raise ValueError(f"missing publisher evidence field: {exc.args[0]}") from exc
    if station_day(STATIONS[station], asof)[0] != day:
        raise ValueError("manifest climate day/asof mismatch")
    if (not isinstance(uri, str) or not any(uri == base or
          (uri.startswith(base) and base.endswith("/"))
          for base in _ALLOWED_PUBLISHERS)):
        raise ValueError("publisher source must be a recognized first-party URL")
    if not isinstance(digest, str) or not _HEX256.fullmatch(digest):
        raise ValueError("publisher evidence SHA-256 missing or invalid")
    if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
        raise ValueError("market ticker evidence missing or invalid")
    return {
        "station": station, "settlement_day": day.isoformat(),
        "asof": asof.isoformat(), "actual_max_f": actual,
        "label_source": f"{uri}#{digest.lower()}",
        "source_market_ticker": ticker,
    }


def export_labeled_days(db_path: Path, evidence_jsonl: Path,
                        output_jsonl: Path, station: str) -> dict:
    """Export only actual as-of-complete NOAA feature snapshots.

    Training remains research-only; callers must independently check every
    first-party publisher record and contract rule against its market.
    """
    if station not in STATIONS:
        raise ValueError("unknown airport station")
    manifest = Path(evidence_jsonl)
    if not manifest.is_file() or manifest.stat().st_size > 15_000_000:
        raise ValueError("publisher manifest missing or too large")
    validated = []
    days = set()
    with manifest.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if number > 100_000:
                raise ValueError("too many publisher records")
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("publisher manifest must contain JSON objects")
            entry = _validate_manifest_row(raw, station)
            if entry is None:
                continue
            if entry["settlement_day"] in days:
                raise ValueError("duplicate publisher settlement day")
            days.add(entry["settlement_day"])
            validated.append(entry)
    if not validated:
        raise ValueError("no publisher evidence records for requested station")
    exported, skipped = [], {}
    for entry in sorted(validated, key=lambda item: item["settlement_day"]):
        snapshot = read_station_forecast(
            db_path, station, _time(entry["asof"]), baseline_only=True)
        if snapshot["status"] != "READY_RESEARCH":
            state = snapshot["status"]
            skipped[state] = skipped.get(state, 0) + 1
            continue
        exported.append({
            "station": station,
            "settlement_day": entry["settlement_day"],
            "asof": entry["asof"],
            "hrrr_max_f": snapshot["hrrr_max_f"],
            "nbm_max_f": snapshot["nbm_max_f"],
            "observed_max_f": snapshot["observed_max_f"],
            "trend_f_per_hour": snapshot["trend_f_per_hour"],
            "hours_to_peak": snapshot["hours_to_peak"],
            "actual_max_f": entry["actual_max_f"],
            "label_source": entry["label_source"],
            "source_market_ticker": entry["source_market_ticker"],
            "source_verification": "MANUAL_REVIEW_REQUIRED",
        })
    if exported:
        target = Path(output_jsonl)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=".weather_labels_", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                for row in exported:
                    handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {
        "mode": "RESEARCH_ONLY", "station": station,
        "exported": len(exported), "skipped": skipped,
        "independently_certified": False,
        "output_file": str(output_jsonl) if exported else None,
        "warning": (
            "Publisher URLs and SHA-256 claims are supplied by the user "
            "and were not independently authenticated. Review market rules "
            "and the original publisher bytes before trusting any label."
        ),
    }
