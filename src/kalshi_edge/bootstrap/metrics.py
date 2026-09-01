from __future__ import annotations

"""Probability metrics and seeded clustered uncertainty estimates."""

import math
import random
from collections import defaultdict
from typing import Hashable, Sequence

from pydantic import BaseModel, ConfigDict, Field


class MetricError(RuntimeError):
    """Raised when probability metrics cannot be computed safely."""


class ReliabilityBin(BaseModel):
    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    count: int = Field(ge=0)
    mean_probability: float | None = None
    observed_rate: float | None = None
    absolute_gap: float | None = None


class ProbabilityMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    brier: float = Field(ge=0.0, le=1.0)
    log_loss: float = Field(ge=0.0)
    accuracy: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    sharpness: float = Field(ge=0.0)
    reliability_bins: tuple[ReliabilityBin, ...]


class BootstrapMetricCI(BaseModel):
    model_config = ConfigDict(frozen=True)

    resamples: int = Field(gt=0)
    brier_low: float
    brier_high: float
    log_loss_low: float
    log_loss_high: float
    accuracy_low: float
    accuracy_high: float
    ece_low: float
    ece_high: float


def _validate_binary_inputs(y_true: Sequence[int], probabilities: Sequence[float]) -> None:
    if len(y_true) != len(probabilities):
        raise MetricError("labels and probabilities must have the same length")
    if not y_true:
        raise MetricError("at least one observation is required")
    if any(label not in (0, 1) for label in y_true):
        raise MetricError("labels must be binary")
    for probability in probabilities:
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise MetricError("probability must be finite and within [0,1]")


def probability_metrics(y_true: Sequence[int], p: Sequence[float]) -> ProbabilityMetrics:
    _validate_binary_inputs(y_true, p)
    count = len(y_true)
    eps = 1e-15

    brier = sum((probability - label) ** 2 for label, probability in zip(y_true, p)) / count
    log_loss = -sum(
        label * math.log(min(1.0 - eps, max(eps, probability)))
        + (1 - label) * math.log(min(1.0 - eps, max(eps, 1.0 - probability)))
        for label, probability in zip(y_true, p)
    ) / count
    accuracy = sum((probability >= 0.5) == bool(label) for label, probability in zip(y_true, p)) / count

    mean_probability = sum(p) / count
    sharpness = math.sqrt(sum((probability - mean_probability) ** 2 for probability in p) / count)

    bins: list[ReliabilityBin] = []
    ece = 0.0
    for index in range(10):
        lower = index / 10.0
        upper = (index + 1) / 10.0
        positions = [
            position
            for position, probability in enumerate(p)
            if probability >= lower and (probability < upper or (index == 9 and probability <= 1.0))
        ]
        if not positions:
            bins.append(ReliabilityBin(lower=lower, upper=upper, count=0))
            continue
        bin_probability = sum(p[position] for position in positions) / len(positions)
        observed_rate = sum(y_true[position] for position in positions) / len(positions)
        gap = abs(bin_probability - observed_rate)
        ece += (len(positions) / count) * gap
        bins.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(positions),
                mean_probability=bin_probability,
                observed_rate=observed_rate,
                absolute_gap=gap,
            )
        )

    return ProbabilityMetrics(
        brier=brier,
        log_loss=log_loss,
        accuracy=accuracy,
        ece=ece,
        sharpness=sharpness,
        reliability_bins=tuple(bins),
    )


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise MetricError("cannot compute percentile of empty values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def clustered_bootstrap_ci(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    cluster_ids: Sequence[Hashable],
    *,
    seed: int,
    resamples: int = 1000,
    confidence: float = 0.95,
) -> BootstrapMetricCI:
    _validate_binary_inputs(y_true, probabilities)
    if len(cluster_ids) != len(y_true):
        raise MetricError("cluster ids must have the same length as labels")
    if resamples <= 0:
        raise MetricError("resamples must be positive")
    if not (0.0 < confidence < 1.0):
        raise MetricError("confidence must be between zero and one")

    positions_by_cluster: dict[Hashable, list[int]] = defaultdict(list)
    cluster_order: list[Hashable] = []
    for position, cluster_id in enumerate(cluster_ids):
        if cluster_id not in positions_by_cluster:
            cluster_order.append(cluster_id)
        positions_by_cluster[cluster_id].append(position)
    if not cluster_order:
        raise MetricError("at least one cluster is required")

    rng = random.Random(seed)
    briers: list[float] = []
    losses: list[float] = []
    accuracies: list[float] = []
    eces: list[float] = []
    for _ in range(resamples):
        sampled_positions: list[int] = []
        for _ in range(len(cluster_order)):
            selected = cluster_order[rng.randrange(len(cluster_order))]
            sampled_positions.extend(positions_by_cluster[selected])
        sample_labels = [y_true[position] for position in sampled_positions]
        sample_probabilities = [probabilities[position] for position in sampled_positions]
        sample_metrics = probability_metrics(sample_labels, sample_probabilities)
        briers.append(sample_metrics.brier)
        losses.append(sample_metrics.log_loss)
        accuracies.append(sample_metrics.accuracy)
        eces.append(sample_metrics.ece)

    alpha = (1.0 - confidence) / 2.0
    return BootstrapMetricCI(
        resamples=resamples,
        brier_low=_percentile(briers, alpha),
        brier_high=_percentile(briers, 1.0 - alpha),
        log_loss_low=_percentile(losses, alpha),
        log_loss_high=_percentile(losses, 1.0 - alpha),
        accuracy_low=_percentile(accuracies, alpha),
        accuracy_high=_percentile(accuracies, 1.0 - alpha),
        ece_low=_percentile(eces, alpha),
        ece_high=_percentile(eces, 1.0 - alpha),
    )
