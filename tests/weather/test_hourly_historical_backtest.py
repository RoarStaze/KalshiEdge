"""Historical archived Kalshi Index replay: synthetic fixtures, never live evidence."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.hourly_historical_backtest import (
    decode_archive_page, archive_index_cases, compare_archived_models,
    download_archived_index, run_archive_research,
)

UTC = timezone.utc
START = datetime(2026, 9, 1, tzinfo=UTC)


def _raw_point(at, value=62.50, *, status="normal"):
    return {
        "t": int(at.timestamp() * 1000), "v": value,
        "status": status, "contributors": 5,
    }


def _fixture_points(days=12):
    points = []
    for minute in range(0, days * 24 * 60, 5):
        time = START + timedelta(minutes=minute)
        # Nontrivial diurnal pattern; purpose is to exercise chronology,
        # NOT to demonstrate skill on real historical weather.
        v = round(59 + 4 * math.sin(2 * math.pi * minute / 1440)
                  + .4 * math.sin(2 * math.pi * minute / 230), 2)
        points.append({
            "event_ms": int(time.timestamp() * 1000),
            "available_ms": int((time + timedelta(minutes=10)).timestamp() * 1000),
            "temp_f": v, "status": "normal", "config_version": "fixture-v1",
        })
    return points


def test_decode_archive_never_claims_original_receipt_time():
    payload = {
        "city": "chicago", "units": "fahrenheit",
        "config_version": "chicago-temperature-v1.1",
        "timeseries": [_raw_point(START), _raw_point(START + timedelta(minutes=5))],
    }
    start = int(START.timestamp() * 1000)
    points = decode_archive_page(payload, "chicago", start, start + 600000)
    assert len(points) == 2
    assert points[0]["available_ms"] == points[0]["event_ms"] + 600000
    assert points[0]["config_version"] == "chicago-temperature-v1.1"
    with pytest.raises(ValueError, match="INVALID_ARCHIVE_SOURCE"):
        decode_archive_page({**payload, "city": "nyc"}, "chicago", start, start + 600000)
    with pytest.raises(ValueError, match="INVALID_ARCHIVE_CONFIG"):
        decode_archive_page({**payload, "config_version": None}, "chicago", start, start + 600000)


def test_archive_rejects_invalid_points_and_ambiguous_versions():
    start = int(START.timestamp() * 1000)
    payload = {
        "city": "chicago", "units": "fahrenheit",
        "config_version": "fixture-v1",
        "timeseries": [
            _raw_point(START, status="unavailable"),
            _raw_point(START + timedelta(minutes=5), value=float("nan")),
            _raw_point(START + timedelta(minutes=10), value=61.234),
        ],
    }
    assert decode_archive_page(payload, "chicago", start, start + 600000) == []
    payload["timeseries"] = [{
        **_raw_point(START), "config_version": "different-v1",
    }]
    with pytest.raises(ValueError, match="INVALID_ARCHIVE_CONFIG"):
        decode_archive_page(payload, "chicago", start, start + 600000)


def test_archived_replay_is_separate_from_frozen_live_evidence():
    points = _fixture_points()
    cases = archive_index_cases(points, START + timedelta(days=12, minutes=12),
                                lead_minutes=30)
    assert len(cases) >= 192
    assert all(row["forecast_asof"] < row["target_utc"] for row in cases)
    assert all(row["label_received_at"] > row["target_utc"] for row in cases)
    assert all("live_frozen_mean_f" not in row for row in cases)
    report = compare_archived_models(cases)
    assert report["status"] == "HISTORICAL_RECONSTRUCTION_SCORED", report
    assert report["matched_hours"] >= 24
    assert report["incumbent"]["mae_f"] is not None
    assert report["candidate"]["mae_f"] is not None
    assert report["incumbent"]["brier"] is None
    assert report["candidate"]["brier"] is None
    assert report["eligible_for_live_promotion"] is False
    assert report["real_orders_enabled"] is False
    assert report["calibration_certified"] is False
    assert report["comparison_identity"] == "RECONSTRUCTED_V12_OBSERVATION_COMPONENT_VS_V13"
    assert report["receipt_assumption"] == "EVENT_TIME_PLUS_10_MINUTES_UNVERIFIED"


def test_archive_replay_never_fabricates_pairs_with_insufficient_hours():
    report = compare_archived_models(archive_index_cases(
        _fixture_points(days=2), START + timedelta(days=2), lead_minutes=30))
    assert report["matched_hours"] == 0
    assert report["status"] == "INSUFFICIENT_ARCHIVED_WALKFORWARD"
    assert report["incumbent"]["mae_f"] is None
    assert report["eligible_for_live_promotion"] is False


def test_archival_request_uses_only_bounded_original_index_windows():
    page = {
        "city": "chicago", "units": "fahrenheit",
        "config_version": "fixture-v1", "timeseries": [],
    }
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=page)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        points, meta = download_archived_index(
            client, "chicago", START, START + timedelta(days=2))
    assert points == []
    assert meta["requests"] == 2
    assert len(calls) == 2
    assert all(x.url.path.endswith("/live_data/weather/chicago") for x in calls)
    assert int(calls[0].url.params["from"]) == int(START.timestamp() * 1000)
    assert int(calls[1].url.params["to"]) == int(
        (START + timedelta(days=2)).timestamp() * 1000) - 1


def test_source_unavailable_is_explicit_not_a_model_defeat():
    def handle(request):
        return httpx.Response(404, json={"error": "history unavailable"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run_archive_research(
            client, cities=("chicago",), start=START,
            end=START + timedelta(days=2), leads=(30,))
    assert report["status"] == "ARCHIVE_SOURCE_UNAVAILABLE"
    assert report["any_promoted"] is False
    assert report["groups"][0]["matched_hours"] == 0
    assert report["groups"][0]["candidate"]["crps"] is None
