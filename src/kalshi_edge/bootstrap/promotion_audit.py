"""Descriptive, read-only post-training evidence; never changes promotion gates.

The one-time lockbox evaluator records paired outcomes before sealing the report.
Legacy reports contain aggregate metrics only; NEVER reconstruct paired results
by re-running an already-consumed lockbox.
"""
from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path
from typing import Any, Sequence

TRADE_ECONOMICS_UNAVAILABLE = {
    "status": "UNAVAILABLE",
    "reason": (
        "The historical 480-second evaluation has labels and model probabilities "
        "but no verified simultaneously executable side-specific ask quotes, "
        "liquidity, fees, slippage, and fill evidence. Net trading returns cannot "
        "be estimated reliably from mid-prices or accuracy alone."
    ),
}


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("paired sample has no observations")
    position = quantile * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def paired_480_evidence(
    market_tickers: Sequence[str],
    labels: Sequence[int],
    candidate_probabilities: Sequence[float],
    incumbent_probabilities: Sequence[float],
    *,
    seed: int = 73115,
    resamples: int = 2000,
    minimum_confidence: float = .69,
) -> dict[str, Any]:
    """One market per row; diagnostic paired bootstrap and exact McNemar test.

    No new look at the evaluation data: called only as part of the existing
    one-time, untouched 480s lockbox evaluation. The output cannot affect
    model promotion decisions and must never be used to choose parameters.
    """
    n = len(market_tickers)
    if n <= 0 or len(labels) != n or len(candidate_probabilities) != n or len(incumbent_probabilities) != n:
        raise ValueError("paired 480s audit requires nonempty equal-length same-market vectors")
    if (any(not isinstance(t, str) or not t for t in market_tickers)
            or len(set(market_tickers)) != n):
        raise ValueError("paired 480s audit requires unique nonempty market tickers")
    if any(type(y) is not int or y not in (0, 1) for y in labels):
        raise ValueError("paired 480s audit requires verified binary settlements")
    for vector in (candidate_probabilities, incumbent_probabilities):
        if any(type(p) not in (float, int) or not math.isfinite(p) or not 0.0 <= p <= 1.0 for p in vector):
            raise ValueError("paired 480s audit requires finite probabilities within [0,1]")
    if type(resamples) is not int or resamples < 1 or resamples > 20000:
        raise ValueError("paired bootstrap resample count invalid")
    if type(minimum_confidence) not in (float, int) or not 0 <= minimum_confidence <= 1:
        raise ValueError("paired confidence threshold invalid")

    c_correct = [(p >= .5) == bool(y) for p, y in zip(candidate_probabilities, labels)]
    i_correct = [(p >= .5) == bool(y) for p, y in zip(incumbent_probabilities, labels)]
    c_qual = [2 * abs(p - .5) >= minimum_confidence for p in candidate_probabilities]
    i_qual = [2 * abs(p - .5) >= minimum_confidence for p in incumbent_probabilities]

    both_correct = sum(c and i for c, i in zip(c_correct, i_correct))
    c_only = sum(c and not i for c, i in zip(c_correct, i_correct))
    i_only = sum(i and not c for c, i in zip(c_correct, i_correct))
    both_wrong = n - both_correct - c_only - i_only

    c_q = sum(c_qual)
    i_q = sum(i_qual)
    both_q = sum(c and i for c, i in zip(c_qual, i_qual))
    c_only_q = c_q - both_q
    i_only_q = i_q - both_q

    # McNemar two-sided exact p: under H0 discordant improvements are 50:50.
    discordant = c_only + i_only
    mcnemar = (
        min(1.0, 2 * sum(math.comb(discordant, k)
                       for k in range(min(c_only, i_only) + 1)) / (1 << discordant))
        if discordant else 1.0
    )

    # The data are matched by market; resample the pair, not the forecasts
    # independently. This is descriptive uncertainty, NOT deployment approval.
    signed = [int(c) - int(i) for c, i in zip(c_correct, i_correct)]
    delta_pp = 100 * (c_only - i_only) / n
    rng = random.Random(seed)
    deltas = [
        100 * sum(signed[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    ]
    low, high = _percentile(deltas, .025), _percentile(deltas, .975)

    return {
        "method": "one_to_one_verified_480s_lockbox",
        "market_count": n,
        "minimum_confidence": float(minimum_confidence),
        "both_correct": both_correct,
        "candidate_only_correct": c_only,
        "incumbent_only_correct": i_only,
        "both_wrong": both_wrong,
        "candidate_only_correct_tickers": [
            market_tickers[index] for index in range(n)
            if c_correct[index] and not i_correct[index]
        ],
        "incumbent_only_correct_tickers": [
            market_tickers[index] for index in range(n)
            if i_correct[index] and not c_correct[index]
        ],
        "candidate_only_qualified_tickers": [
            market_tickers[index] for index in range(n)
            if c_qual[index] and not i_qual[index]
        ],
        "incumbent_only_qualified_tickers": [
            market_tickers[index] for index in range(n)
            if i_qual[index] and not c_qual[index]
        ],
        "candidate_qualified": c_q,
        "incumbent_qualified": i_q,
        "candidate_qualified_correct": sum(c and right for c, right in zip(c_qual, c_correct)),
        "incumbent_qualified_correct": sum(i and right for i, right in zip(i_qual, i_correct)),
        "both_qualified": both_q,
        "candidate_only_qualified": c_only_q,
        "incumbent_only_qualified": i_only_q,
        "neither_qualified": n - c_q - i_q + both_q,
        "candidate_only_qualified_correct": sum(
            c and not i and right
            for c, i, right in zip(c_qual, i_qual, c_correct)
        ),
        "incumbent_only_qualified_correct": sum(
            i and not c and right
            for c, i, right in zip(c_qual, i_qual, i_correct)
        ),
        "accuracy_delta_pp": delta_pp,
        "confidence_interval_95_pp": [low, high],
        "mcnemar_exact_p": mcnemar,
        "paired_uncertainty": {
            "method": "paired_market_bootstrap",
            "confidence": .95,
            "resamples": resamples,
            "seed": seed,
            "excludes_zero": low > 0 or high < 0,
            "interpretation": (
                "Post-hoc, descriptive paired accuracy uncertainty; not a new "
                "promotion threshold or proof of profitable execution."
            ),
        },
        "trade_economics": dict(TRADE_ECONOMICS_UNAVAILABLE),
    }


def _metric_pair(metrics: dict, name1: str, name2: str, metric: str) -> tuple[float, float]:
    try:
        first, second = (float(metrics[name1][metric]), float(metrics[name2][metric]))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("saved evaluation is missing required 480s metrics") from exc
    if not all(math.isfinite(value) for value in (first, second)):
        raise ValueError("nonfinite values in saved 480s report")
    return first, second


def summarize_saved_report(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Use only existing immutable aggregates; no model or lockbox is loaded."""
    if not isinstance(payload, dict) or not isinstance(payload.get("report"), dict):
        raise ValueError("saved evaluation report is malformed")
    report = payload["report"]
    if not report.get("requires_retrain_480_gate"):
        return None
    metrics = report.get("metrics") or {}
    gates = report.get("decision_metrics") or {}
    if not isinstance(metrics, dict) or not isinstance(gates, dict):
        raise ValueError("saved retraining metrics malformed")

    candidate = gates.get("retrain_480_candidate")
    incumbent = gates.get("retrain_480_incumbent")
    if not isinstance(candidate, dict) or not isinstance(incumbent, dict):
        raise ValueError("saved 480s confidence-gate metrics are unavailable")
    try:
        n = int(candidate["total_markets"])
        incumbent_n = int(incumbent["total_markets"])
        cq, iq = int(candidate["qualified_markets"]), int(incumbent["qualified_markets"])
        cc, ic = int(candidate["qualified_correct"]), int(incumbent["qualified_correct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("saved 480s market counts invalid") from exc
    if not (n > 0 and n == incumbent_n and n >= cq >= cc >= 0 and n >= iq >= ic >= 0):
        raise ValueError("saved 480s market counts contradict one another")
    ca, ia = _metric_pair(metrics, "retrain_480_candidate", "retrain_480_incumbent", "accuracy")
    brier_c, brier_i = _metric_pair(metrics, "retrain_480_candidate", "retrain_480_incumbent", "brier")
    log_c, log_i = _metric_pair(metrics, "retrain_480_candidate", "retrain_480_incumbent", "log_loss")
    ece_c, ece_i = _metric_pair(metrics, "retrain_480_candidate", "retrain_480_incumbent", "ece")
    all_c, all_i = _metric_pair(metrics, "candidate", "incumbent_current", "accuracy")
    paired = report.get("paired_480_audit")
    if paired is not None:
        if not isinstance(paired, dict) or paired.get("market_count") != n:
            raise ValueError("saved paired 480s evidence does not match the sealed market count")
        for field, count in (
            ("candidate_only_correct_tickers", paired.get("candidate_only_correct")),
            ("incumbent_only_correct_tickers", paired.get("incumbent_only_correct")),
            ("candidate_only_qualified_tickers", paired.get("candidate_only_qualified")),
            ("incumbent_only_qualified_tickers", paired.get("incumbent_only_qualified")),
        ):
            names = paired.get(field)
            if (type(names) is not list or type(count) is not int
                    or len(names) != count or len(set(names)) != count):
                raise ValueError("paired 480s ticker evidence contradicts sealed counts")
        if (paired.get("candidate_qualified") != cq
                or paired.get("incumbent_qualified") != iq
                or paired.get("candidate_qualified_correct") != cc
                or paired.get("incumbent_qualified_correct") != ic
                or abs((paired.get("both_correct", -1) + paired.get("candidate_only_correct", -1)) / n - ca) > 1e-10
                or abs((paired.get("both_correct", -1) + paired.get("incumbent_only_correct", -1)) / n - ia) > 1e-10):
            raise ValueError("paired 480s evidence contradicts sealed aggregate scores")

    return {
        "status": "PAIRED_AVAILABLE" if paired is not None else "AGGREGATES_ONLY",
        "market_count": n,
        "overall_480_accuracy_candidate": ca,
        "overall_480_accuracy_incumbent": ia,
        "overall_480_accuracy_delta_pp": 100 * (ca - ia),
        "overall_allrows_accuracy_delta_pp": 100 * (all_c - all_i),
        "480_brier_delta": brier_c - brier_i,
        "480_log_loss_delta": log_c - log_i,
        "480_ece_delta": ece_c - ece_i,
        "candidate_qualified": cq,
        "incumbent_qualified": iq,
        "candidate_qualified_correct": cc,
        "incumbent_qualified_correct": ic,
        "candidate_qualified_accuracy": cc / cq if cq else None,
        "incumbent_qualified_accuracy": ic / iq if iq else None,
        "qualified_accuracy_delta_pp": 100 * ((cc / cq) - (ic / iq)) if cq and iq else None,
        "coverage_delta_pp": 100 * (cq - iq) / n,
        "paired_480": paired,
        "paired_status": (
            "Paired outcomes recorded during the one-time lockbox evaluation."
            if paired is not None else
            "Paired outcomes were not recorded for this evaluation. They cannot "
            "be reconstructed from aggregates without replaying the sealed lockbox."
        ),
        "trade_economics": dict(TRADE_ECONOMICS_UNAVAILABLE),
        "decision_unchanged": True,
    }


def load_report_audit(root: Path, report_path: Path) -> dict[str, Any] | None:
    """Only read a local report; refuse arbitrary paths and do not write files."""
    report_dir = (root.resolve() / "reports").resolve()
    path = report_path.resolve()
    if path.parent != report_dir:
        raise ValueError("evaluation report path is outside the verified reports directory")
    if not re.fullmatch(r"evaluation-[0-9a-f]{64}\.json", path.name):
        raise ValueError("saved evaluation filename is not hash-addressed")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return summarize_saved_report(payload)
