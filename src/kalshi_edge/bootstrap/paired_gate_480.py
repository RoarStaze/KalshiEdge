"""Research-only attribution of paired confidence-gate coverage at eight minutes.

Uses exactly matched settled outcomes and point-in-time probabilities.
This is a diagnostic, NOT a gate optimizer, model promotion or trade signal.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

MIN_CONFIDENCE = .69


def _accuracy(correct: int, markets: int) -> float | None:
    return correct / markets if markets else None


def summarize_gate_overlap(
    labels: Sequence[int],
    candidate_probabilities: Sequence[float],
    baseline_probabilities: Sequence[float],
) -> dict[str, Any]:
    """Partition SAME markets by qualification at the unchanged 69% gate.

    A qualifying signal has 2*abs(P(YES)-0.5) >= .69. Exclusive
    cohorts make it possible to distinguish added high-confidence signals
    from those that the specialist no longer qualifies.
    """
    count = len(labels)
    if count == 0 or len(candidate_probabilities) != count or len(baseline_probabilities) != count:
        raise ValueError("confidence comparison needs equal nonempty matched inputs")

    both = candidate_only = baseline_only = neither = 0
    both_candidate_wins = both_baseline_wins = 0
    candidate_only_wins = baseline_only_wins = 0
    for label, candidate, baseline in zip(labels, candidate_probabilities, baseline_probabilities):
        if type(label) is not int or label not in (0, 1):
            raise ValueError("confidence comparison requires binary settled labels")
        if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
               for p in (candidate, baseline)):
            raise ValueError("confidence comparison requires valid probability pairs")
        candidate_qualifies = 2 * abs(candidate - .5) >= MIN_CONFIDENCE
        baseline_qualifies = 2 * abs(baseline - .5) >= MIN_CONFIDENCE
        candidate_correct = int((candidate >= .5) == bool(label))
        baseline_correct = int((baseline >= .5) == bool(label))
        if candidate_qualifies and baseline_qualifies:
            both += 1
            both_candidate_wins += candidate_correct
            both_baseline_wins += baseline_correct
        elif candidate_qualifies:
            candidate_only += 1
            candidate_only_wins += candidate_correct
        elif baseline_qualifies:
            baseline_only += 1
            baseline_only_wins += baseline_correct
        else:
            neither += 1

    return {
        "total_markets": count,
        "minimum_confidence": MIN_CONFIDENCE,
        "both_qualified": {
            "markets": both,
            "candidate_correct": both_candidate_wins,
            "baseline_correct": both_baseline_wins,
            "candidate_accuracy": _accuracy(both_candidate_wins, both),
            "baseline_accuracy": _accuracy(both_baseline_wins, both),
        },
        "candidate_only": {
            "markets": candidate_only,
            "correct": candidate_only_wins,
            "accuracy": _accuracy(candidate_only_wins, candidate_only),
        },
        "baseline_only": {
            "markets": baseline_only,
            "correct": baseline_only_wins,
            "accuracy": _accuracy(baseline_only_wins, baseline_only),
        },
        "neither_qualified": neither,
        "net_new_qualifying_forecasts": candidate_only - baseline_only,
        "net_new_correct_qualifying_forecasts": candidate_only_wins - baseline_only_wins,
        "note": (
            "Hypothetical 69%-gate matched historical cohorts only. "
            "Net_new_correct counts exclusive qualifying cohorts, not total "
            "correct-difference or trade P/L. The historical Kalshi candle "
            "midpoint is not an executable 480s ask. Neither training-only "
            "validation nor this summary establishes live model superiority."
        ),
    }
