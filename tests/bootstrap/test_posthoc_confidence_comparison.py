"""Post-hoc confidence comparison must never disguise replay as fresh validation."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import evaluate, posthoc_confidence_comparison as subject


def inputs():
    labels=[1,0,0,0,1,1,1,0]
    m1=[.91,.09,.83,.17,.79,.21,.60,.40]
    m2=[.87,.13,.81,.19,.82,.18,.76,.24]
    original={
        "retrain_480_candidate":evaluate.checkpoint_gate_snapshot(labels,m2,minimum_confidence=.69),
        "retrain_480_incumbent":evaluate.checkpoint_gate_snapshot(labels,m1,minimum_confidence=.69),
    }
    return labels,m1,m2,original


def test_60percent_same_market_comparison_reproduces_original_69percent_first():
    labels,m1,m2,original=inputs()
    result=subject.compare_probabilities(labels,m1,m2,original)
    assert result["status"]=="DESCRIPTIVE_POSTHOC_REPLAY"
    assert result["market_count"]==8
    assert result["original_69_gate_verified"] is True
    assert result["automatic_promotion_eligible"] is False
    assert result["model_1"]["at_69"]["qualified"]==2
    assert result["model_2"]["at_69"]["qualified"]==2
    assert result["model_1"]["at_60"]["qualified"]==4
    assert result["model_2"]["at_60"]["qualified"]==6
    assert result["model_1"]["at_60"]["skipped"]==4
    assert result["model_2"]["at_60"]["skipped"]==2
    assert result["model_1"]["at_60"]["correct"]==3
    assert result["model_2"]["at_60"]["correct"]==4
    assert result["model_1"]["at_60"]["qualified_accuracy"]==.75
    assert result["model_2"]["at_60"]["qualified_accuracy"]==pytest.approx(4/6)
    assert result["matched_60"]["both_qualified"]==4
    assert result["matched_60"]["model_2_only_qualified"]==2


def test_mismatched_sealed_69percent_counts_fail_closed():
    labels,m1,m2,original=inputs()
    inconsistent=dict(original)
    inconsistent["retrain_480_candidate"]=evaluate.GateSnapshot(
        total_markets=8,qualified_markets=3,qualified_correct=2,minimum_confidence=.69)
    with pytest.raises(ValueError,match="69%|reproduce|mismatch"):
        subject.compare_probabilities(labels,m1,m2,inconsistent)


def test_invalid_probability_or_inconsistent_cohorts_fail_closed():
    labels,m1,m2,original=inputs()
    with pytest.raises(ValueError):
        subject.compare_probabilities(labels,m1,m2[:-1],original)
    with pytest.raises(ValueError):
        subject.compare_probabilities(labels,m1,[float("nan")]+m2[1:],original)
    with pytest.raises(ValueError):
        subject.compare_probabilities(labels,m1,m2,{})


def test_rejects_incorrect_saved_report_boundaries_and_missing_retrain_gate():
    from kalshi_edge.bootstrap.evaluate import BootstrapEvaluationReport
    import base64
    # Only the validation helper is unit-tested here; no lockbox rows are opened.
    expected=SimpleNamespace(
        bundle_sha256="a"*64,
        input_hashes={"derived/features.parquet":"b"*64},
        model_version="test",
        boundaries=SimpleNamespace(lockbox_start_ts_ns=100,lockbox_end_ts_ns=200),
    )
    report=SimpleNamespace(
        bundle_sha256="a"*64,
        dataset_sha256="b"*64,
        model_version="test",
        lockbox_start_ts_ns=100,lockbox_end_ts_ns=200,
        requires_retrain_480_gate=True,
        decision_metrics=inputs()[3],
    )
    assert subject.validate_report_identity(expected,report) is None
    with pytest.raises(ValueError,match="provenance|identity"):
        subject.validate_report_identity(expected,SimpleNamespace(**{**vars(report),"dataset_sha256":"c"*64}))
    with pytest.raises(ValueError,match="480s|retrain"):
        subject.validate_report_identity(expected,SimpleNamespace(**{**vars(report),"requires_retrain_480_gate":False}))


def test_requires_explicit_posthoc_acknowledgement_before_any_file_or_model_access(tmp_path):
    with pytest.raises(ValueError,match="post-hoc|acknowledg"):
        subject.run_comparison(tmp_path,acknowledge_posthoc=False)
    assert list(tmp_path.iterdir())==[]


def test_resolves_model_one_from_default_or_direct_verified_previous_hash(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import artifact, live
    root=tmp_path
    path=root/"models"/"default.json"
    path.parent.mkdir(parents=True)
    current=SimpleNamespace(bundle_sha256="a"*64,stage="promoted",promotion_rule=evaluate.PROMOTION_RULE_ID)
    previous=SimpleNamespace(bundle_sha256="b"*64,stage="promoted",promotion_rule=evaluate.PROMOTION_RULE_ID)
    monkeypatch.setattr(live,"load_default_bundle",lambda _:current)
    monkeypatch.setattr(artifact,"load_model_bundle",lambda _:previous)
    path.write_text(json.dumps({"model_number":1,"bundle_sha256":"a"*64,"promotion_rule":evaluate.PROMOTION_RULE_ID}))
    assert subject.resolve_model_one(root).bundle_sha256=="a"*64
    path.write_text(json.dumps({"model_number":2,"bundle_sha256":"a"*64,
                                "previous_model_hash":"b"*64,
                                "promotion_rule":evaluate.MANUAL_OVERRIDE_RULE_ID}))
    assert subject.resolve_model_one(root).bundle_sha256=="b"*64
    path.write_text(json.dumps({"model_number":3,"bundle_sha256":"a"*64,
                                "previous_model_hash":"b"*64}))
    with pytest.raises(ValueError,match="Model 1|model 1"):
        subject.resolve_model_one(root)


def test_63percent_compares_same_markets_against_original_69pct_reference():
    labels, m1, m2, original = inputs()
    result = subject.compare_probabilities(
        labels, m1, m2, original, threshold=0.63,
    )
    assert result["original_69_gate_verified"] is True
    assert result["thresholds"] == [0.63, 0.69]
    assert result["selected_confidence_threshold"] == 0.63
    assert result["market_count"] == 8
    assert result["model_1"]["at_69"]["qualified"] == 2
    assert result["model_2"]["at_69"]["qualified"] == 2
    assert result["model_1"]["at_63"] == {
        "qualified": 4, "correct": 3, "incorrect": 1, "skipped": 4,
        "coverage": 0.5, "qualified_accuracy": 0.75,
    }
    assert result["model_2"]["at_63"] == {
        "qualified": 4, "correct": 3, "incorrect": 1, "skipped": 4,
        "coverage": 0.5, "qualified_accuracy": 0.75,
    }
    assert result["model_1"]["qualified_added_at_63"] == 2
    assert result["model_2"]["qualified_added_at_63"] == 2
    assert result["matched_63"] == {
        "both_qualified": 2,
        "model_1_only_qualified": 2,
        "model_2_only_qualified": 2,
        "neither_qualified": 2,
    }
    # Output is historical only, never a request to change the live setting.
    assert result["automatic_promotion_eligible"] is False
    assert result["live_confidence_threshold_changed"] is False


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -0.1, 0.0, 0.635, 0.69, 1.0, True])
def test_posthoc_threshold_validation_rejects_unsafe_or_ambiguous_values(threshold):
    labels, m1, m2, original = inputs()
    with pytest.raises(ValueError, match="threshold|confidence"):
        subject.compare_probabilities(
            labels, m1, m2, original, threshold=threshold,
        )


def test_cli_forwards_explicit_63percent_threshold_without_modifying_files(monkeypatch, capsys):
    recorded = []
    def fake_run(root, *, acknowledge_posthoc, threshold):
        recorded.append((acknowledge_posthoc, threshold))
        return {"selected_confidence_threshold": threshold}
    monkeypatch.setattr(subject, "run_comparison", fake_run)
    assert subject.main(["--threshold", "0.63", "--acknowledge-posthoc"]) == 0
    assert recorded == [(True, 0.63)]
    assert json.loads(capsys.readouterr().out)["selected_confidence_threshold"] == 0.63
