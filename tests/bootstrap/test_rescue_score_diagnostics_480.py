"""The fixed 480s precision guard exposes diagnostics without selecting a new rule."""
from __future__ import annotations

import pytest


def _case():
    # 100 original qualifying forecasts; 140 original SKIPs.
    labels = [1]*90+[0]*10+[1]*100+[0]*40
    direction_p = [.95]*100+[.7]*140
    estimated_correct = [.4]*100+[.93]*100+[.88]*40
    return labels, direction_p, estimated_correct


def test_all_original_skips_in_fixed_score_bins_without_leaking_original_qualifiers():
    from kalshi_edge.bootstrap.rescue_score_diagnostics_480 import describe_rescue_scores

    labels, predicted, estimated = _case()
    result = describe_rescue_scores(labels, predicted, estimated)
    assert result["source"] == "DEVELOPMENT_THRESHOLD_SELECTION_OOF_ONLY"
    assert result["original_skips"] == 140
    assert result["original_qualifying_markets_excluded"] == 100
    assert result["skipped_direction_correct"] == 100
    assert result["skipped_direction_wrong"] == 40
    assert sum(b["markets"] for b in result["bins"]) == 140
    assert next(b for b in result["bins"] if b["min_score"] == .875)["markets"] == 40
    assert next(b for b in result["bins"] if b["min_score"] == .925)["markets"] == 100
    assert result["above_90_percent"] == 100
    assert result["score_range"]["min"] == pytest.approx(.88)
    assert result["score_range"]["max"] == pytest.approx(.93)
    assert result["ready_to_promote"] is False


def test_fixed_top_k_ranking_includes_wrong_predictions_and_uncertainty():
    from kalshi_edge.bootstrap.rescue_score_diagnostics_480 import describe_rescue_scores
    labels, predicted, estimated = _case()
    result = describe_rescue_scores(labels, predicted, estimated)
    by_k = {r["requested_top_k"]:r for r in result["top_k"]}
    assert by_k[20]["markets"] == 20
    assert by_k[20]["correct"] == 20
    assert by_k[20]["wilson_95_lower"] < 1
    assert by_k[100]["markets"] == 100
    assert by_k[100]["correct"] == 100
    assert by_k[250]["markets"] == 140
    assert by_k[250]["incorrect"] == 40
    assert result["roc_auc_on_skipped_direction_correctness"] == pytest.approx(1)


def test_zero_skips_and_one_class_are_not_reported_as_perfect_discrimination():
    from kalshi_edge.bootstrap.rescue_score_diagnostics_480 import describe_rescue_scores
    only_qualified = describe_rescue_scores([1,0],[.95,.05],[.99,.99])
    assert only_qualified["original_skips"] == 0
    assert only_qualified["roc_auc_on_skipped_direction_correctness"] is None
    assert only_qualified["skipped_direction_accuracy"] is None
    assert sum(b["markets"] for b in only_qualified["bins"]) == 0
    all_correct = describe_rescue_scores([1,1],[.7,.7],[.8,.9])
    assert all_correct["roc_auc_on_skipped_direction_correctness"] is None


def test_score_probability_validation_refuses_nan_and_mismatch():
    from kalshi_edge.bootstrap.rescue_score_diagnostics_480 import describe_rescue_scores
    with pytest.raises(ValueError):
        describe_rescue_scores([1],[.7],[float("nan")])
    with pytest.raises(ValueError):
        describe_rescue_scores([1,1],[.7],[.8])
    with pytest.raises(ValueError):
        describe_rescue_scores([1],[.7],[1.2])


def test_existing_precision_guard_reports_score_distribution_without_changing_selection():
    from kalshi_edge.bootstrap.precision_guarded_480 import choose_guarded_threshold
    labels,predicted,estimated = _case()
    result = choose_guarded_threshold(labels,predicted,estimated)
    assert result["status"] == "THRESHOLD_SELECTED"
    assert result["selected_threshold"] == .925
    diagnostic = result["score_diagnostics"]
    assert diagnostic["original_skips"] == 140
    assert diagnostic["source"] == "DEVELOPMENT_THRESHOLD_SELECTION_OOF_ONLY"
    assert result["grid"][0]["rescue_markets"] == 140
