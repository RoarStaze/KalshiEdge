from __future__ import annotations

import math

import pytest

from kalshi_edge.bootstrap import metrics


def test_probability_metrics_match_known_binary_example() -> None:
    result = metrics.probability_metrics([0, 1], [0.25, 0.75])

    assert result.brier == pytest.approx(0.0625)
    assert result.log_loss == pytest.approx(-math.log(0.75))
    assert result.accuracy == 1.0
    assert result.ece == pytest.approx(0.25)
    assert result.sharpness == pytest.approx(0.25)
    assert len(result.reliability_bins) == 10
    assert sum(bin_.count for bin_ in result.reliability_bins) == 2


def test_probability_metrics_reject_invalid_inputs() -> None:
    with pytest.raises(metrics.MetricError, match="same length"):
        metrics.probability_metrics([0, 1], [0.5])
    with pytest.raises(metrics.MetricError, match="probability"):
        metrics.probability_metrics([0], [1.1])
    with pytest.raises(metrics.MetricError, match="binary"):
        metrics.probability_metrics([2], [0.5])


def test_clustered_bootstrap_ci_is_seeded_and_cluster_resampled() -> None:
    labels = [0, 0, 1, 1, 0, 0, 1, 1]
    probabilities = [0.1, 0.2, 0.8, 0.9, 0.2, 0.3, 0.7, 0.8]
    clusters = ["day-a", "day-a", "day-b", "day-b", "day-c", "day-c", "day-d", "day-d"]

    first = metrics.clustered_bootstrap_ci(
        labels,
        probabilities,
        clusters,
        seed=73115,
        resamples=128,
    )
    second = metrics.clustered_bootstrap_ci(
        labels,
        probabilities,
        clusters,
        seed=73115,
        resamples=128,
    )

    assert first == second
    assert first.resamples == 128
    assert 0.0 <= first.brier_low <= first.brier_high <= 1.0
    assert 0.0 <= first.accuracy_low <= first.accuracy_high <= 1.0
    assert first.log_loss_low <= first.log_loss_high
