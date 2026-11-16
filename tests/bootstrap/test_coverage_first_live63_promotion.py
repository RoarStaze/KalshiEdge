"""New full-retrain promotion favors fewer SKIPs with tightly bounded accuracy loss.

No orders, private user data, or old lockbox replay.
"""
from __future__ import annotations

import pytest
from kalshi_edge.bootstrap import artifact, evaluate, training


def metric(*, accuracy=.74,brier=.171,loss=.511,ece=.03):
    return artifact.MetricSnapshot(
        accuracy=accuracy,brier=brier,log_loss=loss,ece=ece,sharpness=.15,
    )


def gate(qualified: int,correct: int, *, threshold: float=.69):
    return evaluate.GateSnapshot(
        total_markets=1000,qualified_markets=qualified,
        qualified_correct=correct,minimum_confidence=threshold,
        gate_kind="predicted_side_probability",
    )


def report(*, qualified=(420,378), incumbent=(405,365),
           cand_overall=.74, incumbent_overall=.74,
           cand_brier=.171,cand_loss=.511, cand_ece=.03,
           threshold=.69, schema=True, leak=True,rule=None):
    c=metric(accuracy=cand_overall,brier=cand_brier,loss=cand_loss,ece=cand_ece)
    i=metric(accuracy=incumbent_overall,brier=.168,loss=.500,ece=.025)
    prior=metric(accuracy=.70,brier=.21,loss=.62,ece=.04)
    market=metric(accuracy=.72,brier=.20,loss=.59,ece=.04)
    return evaluate.BootstrapEvaluationReport(
        bundle_sha256="a"*64,dataset_sha256="b"*64,
        model_version=training.MODEL_VERSION,
        lockbox_start_ts_ns=300,lockbox_end_ts_ns=400,
        evaluated_rows=1000,excluded_rows=0,
        feature_schema_match=schema,
        leakage_audit=artifact.LeakageEvidence(
            passed=leak,finding_count=0 if leak else 1,
            finding_codes=() if leak else ("FUTURE_INPUT",),
        ),
        required_ablations_present=True,
        metrics={
            "candidate":c,"kalshi_prior":prior,"incumbent_current":i,
            "retrain_480_candidate":c,
            "retrain_480_incumbent":i,
            "retrain_480_kalshi":market,
        },
        decision_metrics={
            "retrain_480_candidate":gate(*qualified,threshold=threshold),
            "retrain_480_incumbent":gate(*incumbent,threshold=threshold),
        },
        requires_retrain_480_gate=True,
        comparison_rule=rule or evaluate.PROMOTION_RULE_ID,
    )


def test_new_rule_allows_more_forecasts_despite_small_accuracy_decline():
    assert evaluate.PROMOTION_RULE_ID != evaluate.LEGACY_PROMOTION_RULE_ID
    assert evaluate.RETRAIN_LIVE_GATE_CONFIDENCE==.69
    r=report()  # coverage +15 forecasts; qualified accuracy ~-0.12pp
    d=evaluate.promotion_decision(r)
    assert d.promoted is True, d.reason_codes
    assert d.reason_codes==()
    assert d.comparison_rule==evaluate.PROMOTION_RULE_ID


def test_same_evidence_stays_rejected_under_immutable_legacy_rule():
    r=report(qualified=(420,378),incumbent=(405,365),
             threshold=.69,rule=evaluate.LEGACY_PROMOTION_RULE_ID)
    d=evaluate.promotion_decision(r)
    assert not d.promoted
    assert "INCUMBENT_MODEL_NOT_BEATEN" in d.reason_codes
    assert d.comparison_rule==evaluate.LEGACY_PROMOTION_RULE_ID


def test_strict_coverage_gain_is_required():
    r=report(qualified=(405,365),incumbent=(405,365))
    d=evaluate.promotion_decision(r)
    assert not d.promoted
    assert "RETRAIN_480_COVERAGE_NOT_INCREASED" in d.reason_codes


def test_qualified_accuracy_can_fall_half_a_point_but_not_more():
    # Incumbent 365/405 = 90.123%; challenger 375/420 = 89.286% (< floor).
    r=report(qualified=(420,375))
    d=evaluate.promotion_decision(r)
    assert not d.promoted
    assert "RETRAIN_480_QUALIFIED_ACCURACY_OUTSIDE_TOLERANCE" in d.reason_codes


def test_worse_full_market_accuracy_beyond_one_point_blocks():
    d=evaluate.promotion_decision(report(cand_overall=.72))
    assert not d.promoted
    assert "RETRAIN_480_OVERALL_ACCURACY_OUTSIDE_TOLERANCE" in d.reason_codes


@pytest.mark.parametrize("brier,loss",[(.19,.511),(.171,.55)])
def test_material_probability_deterioration_still_blocks(brier,loss):
    d=evaluate.promotion_decision(report(cand_brier=brier,cand_loss=loss))
    assert not d.promoted
    assert "RETRAIN_480_INCUMBENT_PROBABILITY_DEGRADED" in d.reason_codes


def test_material_calibration_deterioration_blocks():
    d=evaluate.promotion_decision(report(cand_ece=.07))
    assert not d.promoted
    assert "RETRAIN_480_INCUMBENT_ECE_DEGRADED" in d.reason_codes


@pytest.mark.parametrize("threshold", [.63,.60])
def test_incorrect_threshold_cannot_promote(threshold):
    d=evaluate.promotion_decision(report(threshold=threshold))
    assert not d.promoted
    assert "RETRAIN_480_CONFIDENCE_GATE_MISMATCH" in d.reason_codes


def test_integrity_gates_still_fail_closed():
    assert "LEAKAGE_AUDIT_FAILED" in evaluate.promotion_decision(
        report(leak=False)
    ).reason_codes
    assert "FEATURE_SCHEMA_MISMATCH" in evaluate.promotion_decision(
        report(schema=False)
    ).reason_codes


def test_no_retroactive_validation_for_old_69_report():
    old=report(threshold=.69,rule=evaluate.LEGACY_PROMOTION_RULE_ID)
    a=evaluate.promotion_decision(old)
    b=evaluate.promotion_decision(old)
    assert a==b and a.comparison_rule==evaluate.LEGACY_PROMOTION_RULE_ID
    assert not a.promoted


def test_old_strength_gate_cannot_disguise_itself_as_live_69_percent_policy():
    r=report()
    gates=dict(r.decision_metrics)
    gates["retrain_480_candidate"]=gates["retrain_480_candidate"].model_copy(
        update={"gate_kind":"model_strength"}
    )
    invalid=r.model_copy(update={"decision_metrics":gates})
    d=evaluate.promotion_decision(invalid)
    assert not d.promoted
    assert "RETRAIN_480_CONFIDENCE_GATE_MISMATCH" in d.reason_codes
