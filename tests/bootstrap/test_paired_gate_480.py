"""Paired 480s checkpoint 69% gate attribution; research-only and no trades."""
import math
import pytest

from kalshi_edge.bootstrap.paired_gate_480 import summarize_gate_overlap


def test_attribution_keeps_only_matched_market_outcomes():
    # six SAME observations: both (0), specialist only (1,2),
    # Kalshi only (3), neither (4,5).
    labels = [1, 0, 1, 0, 1, 0]
    specialist = [.95, .10, .92, .65, .70, .40]
    kalshi = [.90, .60, .40, .90, .50, .30]
    r = summarize_gate_overlap(labels, specialist, kalshi)
    assert r["total_markets"] == 6
    assert r["minimum_confidence"] == .69
    assert r["both_qualified"] == {
        "markets": 1, "candidate_correct": 1, "baseline_correct": 1,
        "candidate_accuracy": 1.0, "baseline_accuracy": 1.0,
    }
    assert r["candidate_only"] == {"markets": 2, "correct": 2, "accuracy": 1.0}
    assert r["baseline_only"] == {"markets": 1, "correct": 0, "accuracy": 0.0}
    assert r["neither_qualified"] == 2
    assert r["net_new_qualifying_forecasts"] == 1
    assert r["net_new_correct_qualifying_forecasts"] == 2
    assert sum((r["both_qualified"]["markets"], r["candidate_only"]["markets"],
                r["baseline_only"]["markets"], r["neither_qualified"])) == 6


def test_unqualified_cohorts_have_unknown_accuracy_not_fake_zero_or_perfect():
    result = summarize_gate_overlap([0, 1], [.5, .5], [.5, .5])
    assert result["both_qualified"]["candidate_accuracy"] is None
    assert result["both_qualified"]["baseline_accuracy"] is None
    assert result["candidate_only"]["accuracy"] is None
    assert result["baseline_only"]["accuracy"] is None
    assert result["net_new_qualifying_forecasts"] == 0


@pytest.mark.parametrize("labels,candidate,baseline", [
    ([], [], []),
    ([1], [.9], []),
    ([True], [.9], [.9]),
    ([2], [.9], [.9]),
    ([1], [float("nan")], [.9]),
    ([1], [float("inf")], [.9]),
    ([1], [True], [.9]),
    ([1], [.9], [-.01]),
    ([1], [.9], [1.01]),
])
def test_attribution_rejects_ambiguous_or_invalid_probabilities(labels, candidate, baseline):
    with pytest.raises(ValueError):
        summarize_gate_overlap(labels, candidate, baseline)


def test_equal_gate_sets_are_not_misreported_as_new_forecasts():
    r = summarize_gate_overlap([1, 0, 1], [.95, .10, .5], [.90, .15, .5])
    assert r["both_qualified"]["markets"] == 2
    assert r["candidate_only"]["markets"] == 0
    assert r["baseline_only"]["markets"] == 0
    assert r["net_new_qualifying_forecasts"] == 0


def test_different_sides_are_scored_against_settlement_not_just_threshold():
    r = summarize_gate_overlap([1], [.95], [.05])
    assert r["both_qualified"]["candidate_correct"] == 1
    assert r["both_qualified"]["baseline_correct"] == 0
