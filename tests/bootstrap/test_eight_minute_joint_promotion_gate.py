"""Promotion may not reduce 480s SKIPs by sacrificing qualified accuracy."""
from kalshi_edge.bootstrap import artifact, evaluate, training
import pytest


def snapshot(*, brier=.14, loss=.41, accuracy=.80):
    return artifact.MetricSnapshot(
        brier=brier, log_loss=loss, accuracy=accuracy,
        ece=.02, sharpness=.15,
    )


def report(*, candidate_accuracy=.80, incumbent_accuracy=.75, gates=None):
    candidate=snapshot(accuracy=candidate_accuracy)
    incumbent=snapshot(brier=.17,loss=.50,accuracy=incumbent_accuracy)
    kalshi=snapshot(brier=.19,loss=.55,accuracy=.72)
    return evaluate.BootstrapEvaluationReport(
        bundle_sha256="a"*64, dataset_sha256="b"*64,
        model_version=training.MODEL_VERSION,
        lockbox_start_ts_ns=100,lockbox_end_ts_ns=200,
        evaluated_rows=80,excluded_rows=0,
        feature_schema_match=True,
        leakage_audit=artifact.LeakageEvidence(passed=True,finding_count=0,finding_codes=()),
        required_ablations_present=True,
        metrics={
            "candidate":candidate,
            "kalshi_prior":kalshi,
            "incumbent_current":incumbent,
            "volatility_480_candidate":candidate,
            "volatility_480_incumbent":incumbent,
            "volatility_480_kalshi":kalshi,
        },
        decision_metrics={} if gates is None else gates,
        comparison_rule=evaluate.PROMOTION_RULE_ID,
    )


def make_gate(eligible=25, correct=23):
    # Distinct, same-market 80-round evaluation cohort.
    return evaluate.GateSnapshot(
        total_markets=80, qualified_markets=eligible,
        qualified_correct=correct,
        minimum_confidence=.69,
    )


def gates(*, candidate=(26,25), incumbent=(20,18)):
    return {
        "volatility_480_candidate":make_gate(*candidate),
        "volatility_480_incumbent":make_gate(*incumbent),
    }


def test_computes_same_threshold_selected_win_rate_with_no_fabricated_trades():
    labels=[1,0,1,0]
    p=[.95,.15,.60,.40]
    gate=evaluate.checkpoint_gate_snapshot(labels,p)
    assert gate.total_markets==4
    assert gate.qualified_markets==2
    assert gate.qualified_correct==2
    assert gate.coverage==.5
    assert gate.qualified_accuracy==1.0
    assert gate.minimum_confidence==.69


def test_candidate_must_reduce_skips_not_just_improve_brier():
    r=report(gates=gates(candidate=(18,18),incumbent=(20,18)))
    verdict=evaluate.promotion_decision(r)
    assert not verdict.promoted
    assert "VOLATILITY_480_COVERAGE_NOT_INCREASED" in verdict.reason_codes


def test_candidate_must_keep_qualified_accuracy_when_coverage_increases():
    # Candidate 30/40=75%, incumbent 18/20=90%: more trades, worse
    # selected hit-rate, despite improved overall probability scores.
    r=report(gates=gates(candidate=(40,30),incumbent=(20,18)))
    verdict=evaluate.promotion_decision(r)
    assert not verdict.promoted
    assert "VOLATILITY_480_QUALIFIED_ACCURACY_DEGRADED" in verdict.reason_codes


def test_candidate_must_increase_overall_directional_accuracy_too():
    r=report(candidate_accuracy=.75,incumbent_accuracy=.75,
             gates=gates(candidate=(26,25),incumbent=(20,18)))
    v=evaluate.promotion_decision(r)
    assert not v.promoted
    assert "VOLATILITY_480_OVERALL_ACCURACY_NOT_IMPROVED" in v.reason_codes


def test_missing_or_inconsistent_gate_evidence_refuses_promotion():
    assert "VOLATILITY_480_CONFIDENCE_GATE_MISSING" in evaluate.promotion_decision(
        report()
    ).reason_codes
    wrong=report(gates=gates(candidate=(26,25),incumbent=(20,18)))
    incorrect=wrong.model_copy(update={"decision_metrics":{
        **wrong.decision_metrics,
        "volatility_480_candidate":evaluate.GateSnapshot(
            total_markets=79,qualified_markets=26,qualified_correct=25,
            minimum_confidence=.69,
        ),
    }})
    assert "VOLATILITY_480_CONFIDENCE_GATE_MISMATCH" in evaluate.promotion_decision(
        incorrect
    ).reason_codes


def test_genuine_joint_improvement_can_clear_gate():
    verdict=evaluate.promotion_decision(report(
        candidate_accuracy=.80,incumbent_accuracy=.75,
        gates=gates(candidate=(26,25),incumbent=(20,18))
    ))
    assert verdict.promoted is True


def test_zero_incumbent_eligible_is_not_evidence_of_relative_qualified_accuracy():
    v=evaluate.promotion_decision(report(gates=gates(candidate=(8,8),incumbent=(0,0))))
    assert not v.promoted
    assert "VOLATILITY_480_QUALIFIED_BASELINE_UNAVAILABLE" in v.reason_codes


@pytest.mark.parametrize("bad", [float("nan"),float("inf"),True,-.1,1.1])
def test_invalid_probability_rejected_by_confidence_gate_scoring(bad):
    with pytest.raises(ValueError):
        evaluate.checkpoint_gate_snapshot([1],[bad])


def test_existing_promotion_reports_without_480_remain_backward_compatible():
    baseline=report()
    old=baseline.model_copy(update={
        "metrics":{k:v for k,v in baseline.metrics.items() if not k.startswith("volatility_480_")}
    })
    assert evaluate.promotion_decision(old).promoted is True
