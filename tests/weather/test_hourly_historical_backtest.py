"""Historical archived Kalshi Index replay: synthetic fixtures, never live evidence."""
from __future__ import annotations

import json
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


@pytest.mark.parametrize("missing_version", [None, ""])
def test_archive_empty_window_without_config_is_not_an_error(missing_version):
    start = int(START.timestamp() * 1000)
    page = {"city": "chicago", "units": "fahrenheit",
            "config_version": missing_version, "timeseries": []}
    assert decode_archive_page(page, "chicago", start, start + 60000) == []


@pytest.mark.parametrize("header_version", ["latest-v2", ""])
def test_archive_uses_calibration_effective_at_event_not_current_header(header_version):
    start = int(START.timestamp() * 1000)
    # One historical range straddles a weekly recalibration; the response
    # header is not a valid per-minute historical config identifier.
    switch = start + 300000
    page = {
        "city": "chicago", "units": "fahrenheit",
        "config_version": header_version,
        "timeseries": [_raw_point(START), _raw_point(START + timedelta(minutes=5))],
    }
    versions = [
        {"config_version": "old-v1", "effective_at_ms": start - 86400000},
        {"config_version": "latest-v2", "effective_at_ms": switch},
    ]
    rows = decode_archive_page(page, "chicago", start, start + 600000,
                               calibrations=versions)
    assert [x["config_version"] for x in rows] == ["old-v1", "latest-v2"]
    assert [x["event_ms"] for x in rows] == [start, switch]


def test_archive_disagrees_with_original_point_config_fails_closed():
    start = int(START.timestamp() * 1000)
    page = {
        "city": "chicago", "units": "fahrenheit",
        "timeseries": [{**_raw_point(START), "config_version": "wrong-v2"}],
    }
    versions = [{"config_version": "actual-v1",
                 "effective_at_ms": start-3600000}]
    with pytest.raises(ValueError, match="ARCHIVE_CALIBRATION_CONFLICT"):
        decode_archive_page(page, "chicago", start, start+60000,
                            calibrations=versions)


def test_archive_download_resolves_missing_header_using_published_timeline():
    start = int(START.timestamp() * 1000)
    requests = []
    def handle(request):
        requests.append(str(request.url))
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(200, json={
                "city": "chicago", "units": "celsius",
                "calibrations": [
                    {"config_version": "v1", "effective_at_ms": start-3600000},
                    {"config_version": "v2", "effective_at_ms": start+86400000},
                ],
            })
        if int(request.url.params["from"]) == start:
            return httpx.Response(200, json={
                "city": "chicago", "units": "fahrenheit",
                "timeseries": [_raw_point(START)],
            })
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit",
            "timeseries": [_raw_point(START+timedelta(days=1))],
        })
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        rows, meta = download_archived_index(
            client, "chicago", START, START+timedelta(days=2))
    assert [r["config_version"] for r in rows] == ["v1", "v2"]
    assert meta["requests"] == 2
    assert meta["calibration_source_sha256"]
    assert len(requests) == 3


def test_missing_calibration_source_cannot_be_relabelled_to_guess():
    def handle(request):
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(404, json={"error": "no history"})
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit",
            "timeseries": [_raw_point(START)],
        })
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run_archive_research(
            client, cities=("chicago",), start=START,
            end=START+timedelta(days=1), leads=(30,))
    assert report["status"] == "ARCHIVE_SOURCE_UNAVAILABLE"
    assert report["groups"][0]["matched_hours"] == 0
    assert report["groups"][0]["candidate"]["mae_f"] is None
    assert "404" in report["groups"][0]["reason"]


def test_no_config_and_no_index_points_reports_no_data_not_invalid_source():
    def handle(request):
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit", "timeseries": [],
        })
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run_archive_research(
            client, cities=("chicago",), start=START,
            end=START+timedelta(days=2), leads=(30,))
    assert report["status"] == "NO_ARCHIVED_INDEX_HISTORY"
    assert report["groups"][0]["status"] == "INSUFFICIENT_ARCHIVED_WALKFORWARD"
    assert report["groups"][0]["matched_hours"] == 0


def test_archive_429_retries_with_retry_after_and_then_scores_real_page(monkeypatch):
    """A transient exchange rate limit must not discard an available page."""
    import kalshi_edge.weather.hourly_historical_backtest as archive
    pauses = []
    monkeypatch.setattr(archive.time, "sleep", lambda seconds: pauses.append(seconds))
    attempts = []
    def handle(request):
        attempts.append(str(request.url))
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit", "timeseries": [],
        })
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        rows, meta = download_archived_index(
            client, "chicago", START, START + timedelta(days=1))
    assert rows == []
    assert len(attempts) == 2
    assert any(delay >= 2 for delay in pauses)
    assert meta["requests"] == 1


def test_archive_cache_resumes_completed_days_after_repeated_429(tmp_path, monkeypatch):
    """An interrupted 90-day run should only refetch windows not yet committed."""
    import kalshi_edge.weather.hourly_historical_backtest as archive
    monkeypatch.setattr(archive.time, "sleep", lambda seconds: None)
    base = int(START.timestamp() * 1000)
    windows = []
    def fail_second(request):
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(200, json={
                "city": "chicago", "units": "celsius",
                "calibrations": [{"config_version": "v1",
                    "effective_at_ms": base-3600000, "published_at_ms": base-7200000}],
            })
        first = int(request.url.params["from"])
        windows.append(first)
        if first != base:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit",
            "timeseries": [_raw_point(START)],
        })
    cache = tmp_path / "archive_cache"
    with httpx.Client(transport=httpx.MockTransport(fail_second)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            download_archived_index(
                client, "chicago", START, START + timedelta(days=2),
                cache_dir=cache)
    assert windows[0] == base
    assert any(x != base for x in windows)
    # Second attempt may read day one from immutable disk cache, no duplicate
    # network request for the already verified historical page.
    resumed = []
    def resume(request):
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(200, json={
                "city": "chicago", "units": "celsius",
                "calibrations": [{"config_version": "v1",
                    "effective_at_ms": base-3600000, "published_at_ms": base-7200000}],
            })
        resumed.append(int(request.url.params["from"]))
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit",
            "timeseries": [_raw_point(START + timedelta(days=1))],
        })
    with httpx.Client(transport=httpx.MockTransport(resume)) as client:
        rows, metadata = download_archived_index(
            client, "chicago", START, START + timedelta(days=2),
            cache_dir=cache)
    assert [r["event_ms"] for r in rows] == [base, base+86400000]
    assert resumed == [base+86400000]
    assert metadata["cache_hits"] == 1
    assert metadata["requests"] == 1


def test_corrupt_archive_cache_fails_closed_without_using_tampered_observations(tmp_path):
    cache = tmp_path / "archive_cache"
    def handle(request):
        return httpx.Response(200, json={
            "city": "chicago", "units": "fahrenheit", "timeseries": [],
        })
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        download_archived_index(client, "chicago", START,
                                START + timedelta(days=1), cache_dir=cache)
    items = list(cache.rglob("*.json"))
    assert len(items) == 1
    saved = json.loads(items[0].read_text(encoding="utf-8"))
    saved["response_sha256"] = "0" * 64
    items[0].write_text(json.dumps(saved), encoding="utf-8")
    def should_not_fetch(request):
        raise AssertionError("Corrupted saved evidence must not be trusted or silently replaced")
    with httpx.Client(transport=httpx.MockTransport(should_not_fetch)) as client:
        with pytest.raises(ValueError, match="ARCHIVE_CACHE_INTEGRITY"):
            download_archived_index(client, "chicago", START,
                                    START + timedelta(days=1), cache_dir=cache)


def test_excessive_retry_after_does_not_send_early_unauthorized_retry(monkeypatch):
    import kalshi_edge.weather.hourly_historical_backtest as archive
    monkeypatch.setattr(archive.time, "sleep",
                        lambda seconds: (_ for _ in ()).throw(AssertionError("Do not sleep")))
    calls = []
    def handle(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "3600"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="ARCHIVE_RATE_LIMIT_RETRY_AFTER_EXCEEDS_CAP"):
            download_archived_index(client, "chicago", START,
                                    START+timedelta(days=1))
    assert len(calls) == 1
