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
    assert result["requested_directional_policy"]["qualified"] == 2
    assert result["requested_directional_policy"]["correct"] == 2
    assert result["requested_directional_policy"]["skipped"] == 3
    assert result["changes"]["extra_qualifying"] == 1
    assert result["changes"]["extra_correct"] == 1
    assert result["changes"]["extra_incorrect"] == 0
    assert result["changes"]["accuracy_change_percentage_points"] == 0
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


def _sweep_fixture(tmp_path):
    # Same frozen probabilities at every gate. Exactly-equal thresholds are
    # SKIP. The 0.91 high-risk record is excluded at every gate but stays in
    # the matched denominator, exactly like the old policy audit.
    examples = [
        (.95, True), (.83, False), (.82, True), (.81, True),
        (.80, False), (.78, True), (.75, False), (.73, True),
        (.70, False), (.68, True), (.63, True), (.60, True),
    ]
    for index, (probability, actual) in enumerate(examples, 1):
        _record(tmp_path, index, p_yes=probability, actual_yes=actual)
    _record(tmp_path, 13, p_yes=.91, actual_yes=True, risk=.10)
    _record(tmp_path, 14, p_yes=.85, actual_yes=True, hash="b" * 64)
    _record(tmp_path, 15, p_yes=.85, actual_yes=True, checkpoint=240)
    _record(tmp_path, 16, p_yes=.85, actual_yes=True, settle=False)
    _record(tmp_path, 17, p_yes=.85, actual_yes=True, reason="STALE_BRTI")


def test_read_only_sweep_fixed_8_thresholds_and_same_paired_cohort(tmp_path):
    from kalshi_edge.bootstrap.forecast_policy_audit_480 import SWEEP_THRESHOLDS_PERCENT
    assert SWEEP_THRESHOLDS_PERCENT == (63, 68, 70, 73, 75, 78, 80, 82)
    _sweep_fixture(tmp_path)
    # Compare directly to the already-shipped policy audit; no parallel
    # sample filtering, silently altered denominator or model mix.
    before = evaluate_saved_policy(tmp_path, model_hash=MODEL)
    report = evaluate_saved_policy(
        tmp_path, model_hash=MODEL,
        sweep_thresholds_percent=SWEEP_THRESHOLDS_PERCENT,
    )
    assert report["matched_settled_markets"] == 13 == before["matched_settled_markets"]
    assert report["excluded"] == before["excluded"]
    assert report["current_strength_policy"] == before["current_strength_policy"]
    assert report["requested_directional_policy"] == before["requested_directional_policy"]
    assert report["model_retraining"] is False
    assert report["live_gate_changed"] is False
    assert report["sweep_thresholds_percent"] == list(SWEEP_THRESHOLDS_PERCENT)
    expected = {
        63: (10, 6, 4),
        68: (9, 5, 4),
        70: (8, 5, 3),
        73: (7, 4, 3),
        75: (6, 4, 2),
        78: (5, 3, 2),
        80: (4, 3, 1),
        82: (2, 1, 1),
    }
    rows = {row["threshold_percent"]: row for row in report["sweep"]}
    assert set(rows) == set(expected)
    for threshold, (qualified, correct, incorrect) in expected.items():
        row = rows[threshold]
        assert row["qualified"] == qualified
        assert row["skipped"] == 13 - qualified
        assert row["correct"] == correct
        assert row["incorrect"] == incorrect
        assert row["accuracy"] == correct / qualified
        assert row["coverage"] == qualified / 13
        assert row["settled_markets"] == 13
        assert row["confidence_interval_kind"] == "not_calculated"
    assert report["requested_directional_policy"]["qualified"] == 9  # Live 69% is distinct from the 63% sweep row


def test_incremental_accuracy_is_correctness_of_extra_band_only(tmp_path):
    _sweep_fixture(tmp_path)
    report = evaluate_saved_policy(
        tmp_path, model_hash=MODEL, sweep_thresholds_percent=(63,68,70,73,75,78,80,82),
    )
    rows = {row["threshold_percent"]: row for row in report["sweep"]}
    # Lowering 82% to 80% adds exactly .82 and .81, both correct.
    assert rows[80]["incremental_vs_higher_threshold_percent"] == 82
    assert rows[80]["incremental_qualified"] == 2
    assert rows[80]["incremental_correct"] == 2
    assert rows[80]["incremental_incorrect"] == 0
    assert rows[80]["incremental_accuracy"] == 1.0
    # Lowering 70% to 68% adds exactly .70, an incorrect prediction.
    assert rows[68]["incremental_vs_higher_threshold_percent"] == 70
    assert rows[68]["incremental_qualified"] == 1
    assert rows[68]["incremental_correct"] == 0
    assert rows[68]["incremental_accuracy"] == 0.
    # Highest threshold has no next-higher configured comparison bucket.
    assert rows[82]["incremental_vs_higher_threshold_percent"] is None
    assert rows[82]["incremental_qualified"] is None
    assert rows[82]["incremental_accuracy"] is None
    # Lowered thresholds never decrease number of qualified forecasts.
    assert all(rows[a]["qualified"] >= rows[b]["qualified"]
               for a, b in zip((63,68,70,73,75,78,80), (68,70,73,75,78,80,82)))


def test_sweep_handles_missing_outcomes_and_no_qualified_predictions(tmp_path):
    from kalshi_edge.bootstrap.forecast_policy_audit_480 import SWEEP_THRESHOLDS_PERCENT
    report = evaluate_saved_policy(
        tmp_path, model_hash=MODEL,
        sweep_thresholds_percent=SWEEP_THRESHOLDS_PERCENT,
    )
    assert report["matched_settled_markets"] == 0
    assert report["status"] == "NO_MATCHED_SETTLEMENTS"
    assert all(row["accuracy"] is None and row["coverage"] == 0
               for row in report["sweep"])
    assert all(row["incremental_accuracy"] is None for row in report["sweep"])
    _record(tmp_path, 1, p_yes=.63, actual_yes=True)
    report = evaluate_saved_policy(
        tmp_path, model_hash=MODEL,
        sweep_thresholds_percent=SWEEP_THRESHOLDS_PERCENT,
    )
    assert report["sweep"][0]["qualified"] == 0
    assert report["sweep"][0]["accuracy"] is None


def test_sweep_cli_text_table_and_json_no_write(tmp_path, capsys):
    import json
    from kalshi_edge.bootstrap.forecast_policy_audit_480 import main
    _sweep_fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert main(["--root", str(tmp_path), "--model-hash", MODEL, "--sweep"]) == 0
    table = capsys.readouterr().out
    assert "DESCRIPTIVE_ONLY" in table
    assert "63%" in table and "82%" in table
    assert "Incremental accuracy" in table
    assert "not a model" in table.lower()
    assert main(["--root", str(tmp_path), "--model-hash", MODEL, "--sweep", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sweep"][0]["threshold_percent"] == 63
    assert report["sweep"][-1]["threshold_percent"] == 82
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_legacy_policy_audit_json_unchanged_without_sweep(tmp_path, capsys):
    import json
    from kalshi_edge.bootstrap.forecast_policy_audit_480 import main
    _record(tmp_path, 1, p_yes=.9, actual_yes=True)
    assert main(["--root", str(tmp_path), "--model-hash", MODEL]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["matched_settled_markets"] == 1
    assert "sweep" not in report
    assert "sweep_thresholds_percent" not in report
