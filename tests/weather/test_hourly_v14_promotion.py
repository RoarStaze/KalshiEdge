"""Weather model version promotions and V14 comparison: synthetic contract tests."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.weather.hourly_historical_backtest import compare_archived_models
from kalshi_edge.weather.hourly_promotion import read_historical_promotions
from kalshi_edge.weather.hourly_v14 import fit_v14, predict_v14, validate_v14

UTC = timezone.utc
NOW = datetime(2026, 11, 28, 3, tzinfo=UTC)


def _synthetic_report(*, mode="WEATHEREDGE_HISTORICAL_REPLAY_V1",
                      identity="RECONSTRUCTED_V12_OBSERVATION_COMPONENT_VS_V13",
                      old_mae=.50, new_mae=.40, old_crps=.42, new_crps=.31,
                      city="chicago", lead=30):
    start = NOW-timedelta(days=90, hours=1)
    end = NOW-timedelta(hours=1)
    group = {
        "city": city, "lead_minutes": lead,
        "status": "HISTORICAL_RECONSTRUCTION_SCORED",
        "comparison_identity": identity,
        "evidence_tier": "HISTORICAL_INDEX_RECONSTRUCTION_UNCERTIFIED",
        "original_publication_times_verified": False,
        "receipt_assumption": "EVENT_TIME_PLUS_10_MINUTES_UNVERIFIED",
        "calibration_certified": False, "real_orders_enabled": False,
        "eligible_for_live_promotion": False,
        "archive_window": [start.isoformat(), end.isoformat()],
        "archive_calibration_versions": 5,
        "archive_calibration_source_sha256": "b"*64,
        "archived_minutes": 18000,
        "retrieval_requests": 90,
        "source_response_sha256": ["a"*64]*90,
        "matched_hours": 96,
        "frozen_live_hours": 0, "verified_market_hours": 0,
        "incumbent": {"hours": 96, "mae_f": old_mae, "crps": old_crps,
                      "coverage_90": .94, "market_hours": 0},
        "candidate": {"hours": 96, "mae_f": new_mae, "crps": new_crps,
                      "coverage_90": .93, "market_hours": 0},
        "folds": [
            {"hours": 24, "incumbent": {"hours": 24, "crps": old_crps},
             "candidate": {"hours": 24, "crps": new_crps}}
            for _ in range(4)],
    }
    return {
        "mode": mode, "status": "HISTORICAL_RECONSTRUCTION_SCORED",
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "asof_utc": NOW.isoformat(), "any_promoted": False,
        "real_orders_enabled": False, "calibration_certified": False,
        "receipt_assumption": "EVENT_TIME_PLUS_10_MINUTES_UNVERIFIED",
        "original_publication_times_verified": False,
        "evidence_tier": "HISTORICAL_INDEX_RECONSTRUCTION_UNCERTIFIED",
        "groups": [group], "scored_groups": 1,
    }


def _write(folder, file, report):
    (folder/file).write_text(json.dumps(report), encoding="utf-8")


def test_historical_v13_temperature_winner_becomes_eligible_only_for_city_lead(tmp_path):
    path = tmp_path/"weatheredge.sqlite3"
    _write(tmp_path, "historical-backtest-v14.json", _synthetic_report())
    found = read_historical_promotions(path, "chicago", 30, asof=NOW)
    assert found["model"] == "v13"
    assert found["evidence_tier"] == "HISTORICAL_INDEX_RECONSTRUCTION_UNCERTIFIED"
    assert found["market_promoted"] is False
    assert read_historical_promotions(path, "miami", 30, asof=NOW)["model"] is None
    assert read_historical_promotions(path, "chicago", 5, asof=NOW)["model"] is None


def test_historical_temperature_promotion_rejects_regression_missing_and_future(tmp_path):
    path = tmp_path/"weatheredge.sqlite3"
    _write(tmp_path, "historical-backtest-remaining.json",
           _synthetic_report(city="nyc", lead=15, old_mae=.37, new_mae=.39))
    assert read_historical_promotions(path, "nyc", 15, asof=NOW)["model"] is None
    good = _synthetic_report()
    good["groups"][0]["matched_hours"] = 0
    _write(tmp_path, "historical-backtest-v14.json", good)
    assert read_historical_promotions(path, "chicago", 30, asof=NOW)["model"] is None
    good = _synthetic_report()
    _write(tmp_path, "historical-backtest-v14.json", good)
    assert read_historical_promotions(path, "chicago", 30,
                                      asof=NOW-timedelta(days=1))["model"] is None


def test_v14_replaces_v13_only_after_an_independent_v13_baseline_win(tmp_path):
    path = tmp_path/"weatheredge.sqlite3"
    _write(tmp_path, "historical-backtest-v14.json", _synthetic_report())
    v14 = _synthetic_report(
        mode="WEATHEREDGE_HISTORICAL_REPLAY_V14_CHALLENGER",
        identity="RECONSTRUCTED_V13_VS_V14",
        old_mae=.42, new_mae=.39, old_crps=.34, new_crps=.30)
    _write(tmp_path, "historical-backtest-v14-vs-v13.json", v14)
    assert read_historical_promotions(path, "chicago", 30, asof=NOW)["model"] == "v14"
    v14["groups"][0]["candidate"]["mae_f"] = .49
    _write(tmp_path, "historical-backtest-v14-vs-v13.json", v14)
    assert read_historical_promotions(path, "chicago", 30, asof=NOW)["model"] == "v13"


def _rows(n=160):
    result = []
    for i in range(n):
        phase = i*.32
        fast = 3*math.sin(phase)
        slow = 1.5*math.sin(phase-.8)
        delta = 2.1*math.sin(i*.53)
        current = 70 + 2*math.cos(i*.19)
        actual = current + .25*fast + .2*slow + .6*delta
        result.append({
            "hours_ahead": .5, "current_index_f": current,
            "fast_slope": fast, "slow_slope": slow,
            "diurnal_delta_f": delta, "baseline_sigma_f": 1.5,
            "baseline_mean_f": current + .55*fast*.5,
            "observed_index_f": actual + .2*math.sin(i*1.7),
        })
    return result


def test_v14_uses_only_current_features_and_validates_parameters():
    rows = _rows()
    fitted = fit_v14(rows)
    assert validate_v14(fitted)
    original = predict_v14(rows[-1], fitted)
    modified = {**rows[-1], "observed_index_f": -99999.}
    assert predict_v14(modified, fitted) == original
    assert all(math.isfinite(v) for v in original)
    assert original[1] > 0
    with pytest.raises(ValueError):
        fit_v14(rows[:20])
    with pytest.raises(ValueError):
        predict_v14(rows[-1], {"bad": True})


def test_v14_archived_head_to_head_never_fabricates_trading_accuracy():
    from kalshi_edge.weather.hourly_historical_backtest import archive_index_cases
    from test_hourly_historical_backtest import _fixture_points, START
    cases = archive_index_cases(
        _fixture_points(days=12), START+timedelta(days=12,minutes=12),
        lead_minutes=30)
    report = compare_archived_models(cases, challenger="v14")
    assert report["status"] == "HISTORICAL_RECONSTRUCTION_SCORED", report
    assert report["comparison_identity"] == "RECONSTRUCTED_V13_VS_V14"
    assert report["matched_hours"] >= 24
    assert report["incumbent"]["crps"] is not None
    assert report["candidate"]["crps"] is not None
    assert report["incumbent"]["brier"] is None
    assert report["eligible_for_live_promotion"] is False


def test_promoted_v13_uses_only_locally_received_earlier_labels():
    from kalshi_edge.weather.hourly_promotion import promote_local_nowcast
    rows = _rows(192)
    base = datetime(2026, 11, 1, tzinfo=UTC)
    cases = []
    for i, row in enumerate(rows):
        when = base + timedelta(hours=i)
        cases.append({
            **row,
            "target_utc": when.isoformat(),
            "forecast_asof": (when-timedelta(minutes=30)).isoformat(),
            "label_received_at": (when+timedelta(minutes=10)).isoformat(),
        })
    decision = {"enabled": False, "model": None, "sigma_scale": 1.,
                "live_slopes": (1., 1., 70., 1)}
    promoted = promote_local_nowcast(
        decision, cases, promoted_version="v13", live_delta=1.5)
    assert promoted["model"] == "REGULARIZED_HOURLY_HYBRID_V13", promoted
    assert promoted["enabled"] is True
    assert promoted["historical_temperature_status"] == "ACTIVE_OBSERVATION_COMPONENT_ONLY"
    assert promoted["historical_temperature_validation"]["market_verified_hours"] == 0
    assert decision["enabled"] is False
    missing_feature = promote_local_nowcast(
        decision, cases, promoted_version="v13", live_delta=None)
    assert missing_feature["enabled"] is False


def test_archived_temperature_promotion_does_not_override_future_market_gate():
    from kalshi_edge.weather.hourly_promotion import promote_local_nowcast
    result = promote_local_nowcast(
        {"enabled": False, "model": None, "live_slopes": None}, [],
        promoted_version="v14", live_delta=None)
    assert result["enabled"] is False
    assert result["historical_temperature_status"] == "WAITING_FOR_LIVE_RECEIPT_SAFE_TRAINING"


def test_v13_can_deploy_from_archived_winner_with_64_current_receipt_safe_training_hours():
    from kalshi_edge.weather.hourly_promotion import promote_local_nowcast
    base = datetime(2026, 11, 14, tzinfo=UTC)
    rows = []
    for i, row in enumerate(_rows(80)):
        when = base + timedelta(hours=i)
        rows.append({**row, "target_utc": when.isoformat(),
                     "forecast_asof": (when-timedelta(minutes=30)).isoformat(),
                     "label_received_at": (when+timedelta(minutes=10)).isoformat()})
    incumbent = {"enabled": False, "model": None, "sigma_scale": 1.,
                 "live_slopes": (1., 1., 70., 1)}
    deployed = promote_local_nowcast(
        incumbent, rows, promoted_version="v13", live_delta=1.4)
    assert deployed["enabled"] is True
    assert deployed["model"] == "REGULARIZED_HOURLY_HYBRID_V13"
    assert deployed["historical_temperature_validation"]["hours"] == 0
    assert deployed["historical_temperature_status"] == "ACTIVE_ARCHIVE_EVIDENCE_ONLY"
    undertrained = promote_local_nowcast(
        incumbent, rows[:63], promoted_version="v13", live_delta=1.4)
    assert undertrained["enabled"] is False


def test_live_nowcast_wires_archived_v13_winner_into_current_forecast(tmp_path, monkeypatch):
    import sqlite3
    from kalshi_edge.weather import hourly_nowcast as hn
    db = tmp_path/"weatheredge.sqlite3"
    _write(tmp_path, "historical-backtest-v14.json", _synthetic_report())
    base = NOW-timedelta(days=4)
    samples = []
    for i, row in enumerate(_rows(80)):
        when = base+timedelta(hours=i)
        samples.append({**row,
                        "target_utc": when.isoformat(),
                        "forecast_asof": (when-timedelta(minutes=30)).isoformat(),
                        "label_received_at": (when+timedelta(minutes=10)).isoformat()})
    current_point = int((NOW-timedelta(minutes=2)).timestamp()*1000)
    points = [(current_point,current_point+1000,70.,"normal")]
    monkeypatch.setattr(hn, "_load_points", lambda *args, **kwargs: (points,None))
    monkeypatch.setattr(hn, "_cases_from_points", lambda *args, **kwargs: samples)
    monkeypatch.setattr(hn, "_slopes_from_points", lambda *args, **kwargs:
                        (1.,.5,70.,current_point))
    monkeypatch.setattr(hn, "_diurnal_delta", lambda *args, **kwargs: 1.4)
    monkeypatch.setattr(hn, "choose_index_nowcast", lambda *args, **kwargs: {
        "enabled": False, "model": None, "history_hours": 80,
        "status": "INDEX_BASELINE", "validation": None, "sigma_scale": 1.})
    with sqlite3.connect(db) as conn:
        forecast=hn.index_nowcast_decision(
            conn, "chicago", NOW, lead_minutes=30, config_version="current-v1",
            target_utc=NOW+timedelta(minutes=30))
    assert forecast["model"] == hn.HYBRID_MODEL, forecast
    assert forecast["status"] == "ARCHIVED_TEMPERATURE_V13_PROMOTED_UNCERTIFIED"
    assert forecast["historical_temperature_status"] == "ACTIVE_ARCHIVE_EVIDENCE_ONLY"
    assert forecast["validation"] is None
    assert forecast["paired_comparison_status"] == "NO_COMPARISON_REPORT"
