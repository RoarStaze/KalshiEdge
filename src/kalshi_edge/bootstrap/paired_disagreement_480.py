"""Strict paired 480s direction/gate overlap, research-only and read-only.

Reports exactly which immutable development observations qualify under the
same fixed 69% confidence gate, and how net correct/incorrect differences
decompose. Unissued preferences are never scored as actual forecasts.
"""
from __future__ import annotations

from typing import Any, Sequence

from .reliability_480_core import BASELINE_GATE, gate_metrics


CATEGORIES = (
    "both_qualified_agree",
    "both_qualified_disagree",
    "logistic_only",
    "nonlinear_only",
    "neither_qualified",
)


def _empty_category() -> dict[str, int]:
    return {
        "markets": 0,
        "logistic_issued": 0,
        "logistic_correct": 0,
        "logistic_incorrect": 0,
        "nonlinear_issued": 0,
        "nonlinear_correct": 0,
        "nonlinear_incorrect": 0,
        "directions_agree": 0,
    }


def paired_disagreement_480(
    labels: Sequence[int],
    logistic_p_yes: Sequence[float],
    nonlinear_p_yes: Sequence[float],
) -> dict[str, Any]:
    """All cases are counted once; comparison uses original recorded outcomes.

    The logistic-preferred union is one *fixed hypothetical* rule: keep the
    logistic direction whenever it qualifies, otherwise add a nonlinear
    direction only when the nonlinear qualifies. It is NOT a deployed or
    selected rule and these development labels are not future validation.
    """
    if not labels:
        raise ValueError("paired disagreement requires at least one market")
    if not (len(labels) == len(logistic_p_yes) == len(nonlinear_p_yes)):
        raise ValueError("paired predictions and outcomes must have equal length")

    # gate_metrics is the same strict validation and scoring contract used
    # elsewhere; it rejects malformed labels and probabilities.
    gate_metrics(labels, logistic_p_yes, [True] * len(labels))
    gate_metrics(labels, nonlinear_p_yes, [True] * len(labels))

    logistic_gate = [
        2 * abs(p - .5) >= BASELINE_GATE for p in logistic_p_yes
    ]
    nonlinear_gate = [
        2 * abs(p - .5) >= BASELINE_GATE for p in nonlinear_p_yes
    ]

    categories = {key: _empty_category() for key in CATEGORIES}
    crosswalk = {
        "nonlinear_only_correct": 0,
        "nonlinear_only_incorrect": 0,
        "logistic_only_correct": 0,
        "logistic_only_incorrect": 0,
        "both_disagree_nonlinear_correct": 0,
        "both_disagree_logistic_correct": 0,
    }
    union_qualified: list[bool] = []
    union_probabilities: list[float] = []

    for outcome, p_log, p_nonlinear, q_log, q_nonlinear in zip(
        labels, logistic_p_yes, nonlinear_p_yes,
        logistic_gate, nonlinear_gate,
    ):
        log_side = p_log >= .5
        nonlin_side = p_nonlinear >= .5
        agree = log_side == nonlin_side
        if q_log and q_nonlinear:
            key = "both_qualified_agree" if agree else "both_qualified_disagree"
        elif q_log:
            key = "logistic_only"
        elif q_nonlinear:
            key = "nonlinear_only"
        else:
            key = "neither_qualified"
        category = categories[key]
        category["markets"] += 1
        category["directions_agree"] += int(agree)

        if q_log:
            correct = log_side == bool(outcome)
            category["logistic_issued"] += 1
            category["logistic_correct"] += int(correct)
            category["logistic_incorrect"] += int(not correct)
        if q_nonlinear:
            correct = nonlin_side == bool(outcome)
            category["nonlinear_issued"] += 1
            category["nonlinear_correct"] += int(correct)
            category["nonlinear_incorrect"] += int(not correct)

        if key == "logistic_only":
            crosswalk[
                "logistic_only_correct" if log_side == bool(outcome)
                else "logistic_only_incorrect"
            ] += 1
        elif key == "nonlinear_only":
            crosswalk[
                "nonlinear_only_correct" if nonlin_side == bool(outcome)
                else "nonlinear_only_incorrect"
            ] += 1
        elif key == "both_qualified_disagree":
            crosswalk[
                "both_disagree_logistic_correct"
                if log_side == bool(outcome)
                else "both_disagree_nonlinear_correct"
            ] += 1

        union_qualified.append(q_log or q_nonlinear)
        union_probabilities.append(p_log if q_log else p_nonlinear)

    logistic = gate_metrics(labels, logistic_p_yes, logistic_gate)
    nonlinear = gate_metrics(labels, nonlinear_p_yes, nonlinear_gate)
    union = gate_metrics(labels, union_probabilities, union_qualified)

    delta_correct = nonlinear["correct"] - logistic["correct"]
    delta_wrong = nonlinear["incorrect"] - logistic["incorrect"]
    delta_qualified = nonlinear["qualified"] - logistic["qualified"]
    if (
        delta_correct != (
            crosswalk["nonlinear_only_correct"]
            - crosswalk["logistic_only_correct"]
            + crosswalk["both_disagree_nonlinear_correct"]
            - crosswalk["both_disagree_logistic_correct"]
        )
        or delta_wrong != (
            crosswalk["nonlinear_only_incorrect"]
            - crosswalk["logistic_only_incorrect"]
            + crosswalk["both_disagree_logistic_correct"]
            - crosswalk["both_disagree_nonlinear_correct"]
        )
        or delta_qualified != (
            categories["nonlinear_only"]["markets"]
            - categories["logistic_only"]["markets"]
        )
        or sum(v["markets"] for v in categories.values()) != len(labels)
    ):
        raise ValueError("paired category totals did not reconcile")

    return {
        "report": "KXBTC15M_480S_PAIRED_MODEL_DISAGREEMENT_V1",
        "markets": len(labels),
        "fixed_gate": BASELINE_GATE,
        "categories": categories,
        "change_decomposition": crosswalk,
        "baseline_logistic": logistic,
        "candidate_nonlinear": nonlinear,
        "logistic_preferred_union": union,
        "net_qualifying_change": delta_qualified,
        "net_correct_change": delta_correct,
        "net_incorrect_change": delta_wrong,
        "ready_to_promote": False,
        "independent_validation": False,
        "note": (
            "This is a descriptive breakdown of already-inspected "
            "development data, NOT an independent or deployable ensemble. "
            "Unissued SKIPs are never included in forecast correctness; "
            "logistic-preferred union is fixed before inspecting outcomes "
            "and only hypothetical. Neither comparator is live V5. "
            "No trading, fees, liquidity, or execution evidence."
        ),
    }
