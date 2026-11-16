"""Paired, read-only historical gate comparison using actual saved checkpoint calls.

This checks decision policy and coverage. It does NOT retrain a model, change
forecasts, or claim that a changed predictive model beat the existing one.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.forecast_policy_audit_480 import evaluate_saved_policy

NS = 1_000_000_000
MODEL = "a" * 64
OPEN = 1_900_000_000 * NS


def _record(root, index, *, p_yes, actual_yes, risk=None,
            hash=MODEL, checkpoint=480, settle=True, reason=None):
    now = OPEN + index * 900 * NS
    close = now + checkpoint * NS
    strength = 2 * abs(p_yes - .5)
    side = "ABOVE" if p_yes >= .5 else "BELOW"
    old_qualified = strength >= .63 and not (risk is not None and risk <= .20)
    forecast = side if old_qualified else "SKIP"
    why = None if old_qualified else (
        "COIN_FLIP_RISK_HIGH" if risk is not None and risk <= .20
        else "CONFIDENCE_BELOW_63_PERCENT"
    )
    record = FinalForecastRecord(
        market_ticker=f"KXBTC15M-POLICY-{index:04d}",
        strike=64000., scheduled_ts_ns=now, prediction_ts_ns=now,
        checkpoint_seconds=checkpoint,
        seconds_remaining=float(checkpoint),
        model_hash=hash, forecast=forecast, reason=reason or why,
        p_yes=p_yes, p_no=1-p_yes,
        confidence=strength, directional_probability=max(p_yes, 1-p_yes),
        coin_flip_risk_z=risk,
        coin_flip_assessment=("HIGH" if risk is not None and risk <= .20 else "LOW"),
    )
    FinalForecastWriter(root).save(record, market_close_ts_ns=close)
    if settle:
        _save_settlement(root, record, OfficialSettlement(
            market_ticker=record.market_ticker, market_close_ts_ns=close,
            result="yes" if actual_yes else "no",
            actual_side="ABOVE" if actual_yes else "BELOW",
            source="kalshi-public-market-api",
            fetched_ts_ns=close + 60 * NS, market_status="settled",
        ))
    return record


def test_policy_audit_reports_more_qualifying_forecasts_and_actual_accuracy(tmp_path):
    _record(tmp_path, 1, p_yes=.7838, actual_yes=True)
    _record(tmp_path, 2, p_yes=.66, actual_yes=False)
    _record(tmp_path, 3, p_yes=.63, actual_yes=True)
    _record(tmp_path, 4, p_yes=.94, actual_yes=True)
    _record(tmp_path, 5, p_yes=.95, actual_yes=False, risk=.10)
    result = evaluate_saved_policy(tmp_path, model_hash=MODEL)
    assert result["status"] == "DESCRIPTIVE_ONLY"
    assert result["matched_settled_markets"] == 5
    assert result["current_strength_policy"]["qualified"] == 1
    assert result["current_strength_policy"]["correct"] == 1
    assert result["requested_directional_policy"]["qualified"] == 3
    assert result["requested_directional_policy"]["correct"] == 2
    assert result["requested_directional_policy"]["skipped"] == 2
    assert result["changes"]["extra_qualifying"] == 2
    assert result["changes"]["extra_correct"] == 1
    assert result["changes"]["extra_incorrect"] == 1
    assert result["changes"]["accuracy_change_percentage_points"] < 0
    assert result["probability_source"] == "UNCHANGED_SAVED_MODEL"
    assert result["model_retraining"] is False


def test_policy_audit_excludes_unusable_forecasts_and_unrelated_model_hash(tmp_path):
    _record(tmp_path, 1, p_yes=.90, actual_yes=True)
    _record(tmp_path, 2, p_yes=.90, actual_yes=True, hash="b"*64)
    _record(tmp_path, 3, p_yes=.85, actual_yes=True, checkpoint=240)
    _record(tmp_path, 4, p_yes=.85, actual_yes=True, settle=False)
    _record(tmp_path, 5, p_yes=.85, actual_yes=True, reason="STALE_BRTI")
    result = evaluate_saved_policy(tmp_path, model_hash=MODEL)
    assert result["matched_settled_markets"] == 1
    assert result["excluded"]["different_model"] == 1
    assert result["excluded"]["non_480_checkpoint"] == 1
    assert result["excluded"]["unsettled"] == 1
    assert result["excluded"]["non_probability_skip"] == 1


def test_policy_audit_has_no_data_to_validate_new_signal_weights(tmp_path):
    result = evaluate_saved_policy(tmp_path, model_hash=MODEL)
    assert result["status"] == "NO_MATCHED_SETTLEMENTS"
    assert result["requested_directional_policy"]["accuracy"] is None
    assert "not a comparison of trained predictors" in result["limitation"].lower()
