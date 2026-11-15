"""Regression: new 480-second research output must use actual live 63% gate.

Historical saved 69%-gate snapshots must retain their original label.
"""
from __future__ import annotations

from kalshi_edge.bootstrap import forward_learning_480, training_cycle
from kalshi_edge.bootstrap.final_forecast import MINIMUM_FINAL_CONFIDENCE
from kalshi_edge.bootstrap.dashboard_ui import PAGE


def test_63percent_model_confidence_is_shared_with_forward_research():
    assert MINIMUM_FINAL_CONFIDENCE == .63
    assert forward_learning_480.CONFIDENCE_GATE == MINIMUM_FINAL_CONFIDENCE
    labels=[1,0,1]
    probabilities=[.82,.18,.80]
    result=forward_learning_480._gate(labels,probabilities)
    assert result["qualified"] == 2
    assert result["correct"] == 2
    assert result["skipped"] == 1


def test_research_reasons_read_generic_63percent_gate():
    report={
        "status":"NO_VALIDATED_JOINT_GAIN",
        "gate_confidence":.63,
        "candidate_gate":{"qualified":36,"correct":34,"coverage":36/191,"qualified_accuracy":34/36},
        "incumbent_gate":{"qualified":65,"correct":61,"coverage":65/191,"qualified_accuracy":61/65},
        "matched_validation_markets":191,
        "candidate_probability_metrics":{"brier":.1636,"log_loss":.4988},
        "incumbent_probability_metrics":{"brier":.1600,"log_loss":.4849},
    }
    reason=training_cycle._forward_failure_reasons(report)
    assert any("36/191" in part for part in reason)
    assert any("coverage" in part.lower() for part in reason)
    assert any("Brier" in part for part in reason)


def test_new_forward_result_does_not_masquerade_as_historical_69_gate():
    # Only old stored reports with 69% keys may display 69%.
    assert "r.candidate_gate" in PAGE
    assert "r.gate_confidence" in PAGE
    assert "candidate_69_gate" not in forward_learning_480.evaluate_forward_candidate.__code__.co_consts
