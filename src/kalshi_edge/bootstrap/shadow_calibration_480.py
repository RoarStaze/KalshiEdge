"""FIXED chronological, read-only, 480-second Platt recalibration SHADOW.

This module NEVER saves a calibrator, updates promoted bundles, alters
live confidence, changes a trading gate, or executes orders.

All replayed markets belong to already inspected history. The final evaluation
block is later than the fitted block, but remains RETROSPECTIVE and is NOT an
independent untouched external lockbox.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import json
import math
from typing import Any, Mapping, Sequence

from .metrics import probability_metrics
from .shadow_eight_minute import replay_eight_minute_history

TRAIN_MARKETS = 60
MIN_EVAL_MARKETS = 20
CHECKPOINT_SECONDS = 480
CHECKPOINT_WINDOW_NS = 2 * 1_000_000_000
MINIMUM_CONFIDENCE = 0.69
SOURCE = "ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS"
EPSILON = 1e-4


def _close_ns(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        return round(dt.timestamp() * 1_000_000_000)
    except (ValueError, TypeError, OverflowError):
        return None


def _valid(row: Mapping[str, Any]) -> bool:
    if row.get("source") != SOURCE or type(row.get("checkpoint_seconds")) is not int:
        return False
    if row["checkpoint_seconds"] != CHECKPOINT_SECONDS:
        return False
    if row.get("settlement_status") != "SETTLED" or row.get("actual_side") not in ("ABOVE", "BELOW"):
        return False
    if not isinstance(row.get("market_ticker"), str) or not row["market_ticker"]:
        return False
    if not isinstance(row.get("model_hash"), str) or not row["model_hash"]:
        return False
    due, recorded = row.get("scheduled_checkpoint_ts_ns"), row.get("recorded_prediction_ts_ns")
    close = _close_ns(row.get("market_close"))
    if (type(due) is not int or type(recorded) is not int
            or close is None or due <= 0
            or close != due + CHECKPOINT_SECONDS * 1_000_000_000
            or not due <= recorded <= due + CHECKPOINT_WINDOW_NS):
        return False

    p, q, confidence = row.get("p_yes"), row.get("p_no"), row.get("confidence")
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (p, q, confidence)):
        return False
    if not (0 <= p <= 1 and 0 <= q <= 1 and 0 <= confidence <= 1):
        return False
    if abs(p + q - 1) > 1e-6 or abs(confidence - 2*abs(p-.5)) > .005:
        return False
    expected_side = "ABOVE" if p >= .5 else "BELOW"
    if confidence < MINIMUM_CONFIDENCE:
        return row.get("forecast") == "SKIP" and row.get("reason") == "CONFIDENCE_BELOW_69_PERCENT"
    return row.get("forecast") == expected_side and row.get("reason") is None


def _logit(p: float) -> float:
    p = max(EPSILON, min(1-EPSILON, p))
    return math.log(p / (1-p))


def _proper_metrics(labels: list[int], probabilities: list[float]) -> dict[str, Any]:
    return {"markets": len(labels), **probability_metrics(labels, probabilities).model_dump(mode="json")}


def _gate(labels: list[int], probabilities: list[float]) -> dict[str, Any]:
    chosen = [i for i, p in enumerate(probabilities) if 2*abs(p-.5) >= MINIMUM_CONFIDENCE]
    wins = sum((probabilities[i] >= .5) == bool(labels[i]) for i in chosen)
    return {
        "eligible_markets": len(chosen),
        "wins": wins,
        "accuracy": wins / len(chosen) if chosen else None,
        "coverage": len(chosen) / len(probabilities) if probabilities else None,
        "note": "Hypothetical confidence gate on retrospectively observed markets, not filled orders.",
    }


def _quote_midpoint(row: Mapping[str, Any]) -> float | None:
    bid, ask = row.get("kalshi_yes_bid"), row.get("kalshi_yes_ask")
    if type(bid) not in (float, int) or type(ask) not in (float, int):
        return None
    if not (math.isfinite(bid) and math.isfinite(ask) and 0 <= bid <= ask <= 1):
        return None
    return (bid + ask)/2


def assess_480_shadow_calibration(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fit fixed 60 chronological old markets, evaluate on later >=20 only."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("expected replay market rows")
    valid = [row for row in rows if isinstance(row, Mapping) and _valid(row)]
    count = Counter(row["market_ticker"] for row in valid)
    duplicate_excluded = sum(n for n in count.values() if n > 1)
    distinct = sorted(
        (row for row in valid if count[row["market_ticker"]] == 1),
        key=lambda row: (row["scheduled_checkpoint_ts_ns"], row["market_ticker"]),
    )
    hashes = dict(sorted(Counter(row["model_hash"] for row in distinct).items()))
    report: dict[str, Any] = {
        "report": "KXBTC15M_FIXED_480S_CHRONOLOGICAL_CALIBRATION_SHADOW_V1",
        "status": "INSUFFICIENT_MARKETS",
        "eligible_markets": len(distinct),
        "excluded_invalid_records": len(rows) - len(valid),
        "excluded_duplicate_records": duplicate_excluded,
        "model_hash_counts": hashes,
        "calibrator": "SIGMOID_LOGIT_PLATT_C1_FROZEN",
        "training_markets": 0, "evaluation_markets": 0,
        "train_last_market": None, "eval_first_market": None,
        "incumbent": None, "candidate": None,
        "incumbent_69_gate": None, "candidate_69_gate": None,
        "market_midpoint": None, "incumbent_matched_market": None,
        "candidate_matched_market": None, "matched_kalshi_midpoint_markets": 0,
        "model_promotion": False,
        "live_trades": False, "independent_validation": False,
        "warning": (
            "RETROSPECTIVE and ALREADY INSPECTED replay; chronological fitting "
            "does NOT restore independent external validation. This candidate "
            "is not a live trading signal, approved probability improvement, "
            "or profit prediction. Do not change confidence/edge gates or "
            "stake size based on this result. Only separate future untouched "
            "480s markets and actual fill economics can justify promotion."
        ),
    }

    if len(hashes) > 1:
        report["status"] = "MIXED_MODEL_HASHES"
        return report
    if len(distinct) < TRAIN_MARKETS + MIN_EVAL_MARKETS:
        return report

    train, evaluation = distinct[:TRAIN_MARKETS], distinct[TRAIN_MARKETS:]
    report["training_markets"] = len(train)
    report["evaluation_markets"] = len(evaluation)
    report["train_last_market"] = train[-1]["market_ticker"]
    report["eval_first_market"] = evaluation[0]["market_ticker"]

    # Even a one-sided historical base rate cannot identify meaningful slope.
    y_train = [int(row["actual_side"] == "ABOVE") for row in train]
    if len(set(y_train)) < 2:
        report["status"] = "INSUFFICIENT_TRAIN_LABEL_VARIATION"
        return report

    from sklearn.linear_model import LogisticRegression

    estimator = LogisticRegression(
        C=1.0, solver="lbfgs", max_iter=1000, random_state=0,
    )
    estimator.fit([[_logit(float(row["p_yes"]))] for row in train], y_train)
    # A negative slope would invert the model's ranking, invalidating any
    # "raise confidence" interpretation; never use that as a shadow candidate.
    slope = float(estimator.coef_[0][0])
    if not math.isfinite(slope) or slope <= 0:
        report["status"] = "NON_MONOTONE_SHADOW_CANDIDATE"
        return report

    incumbent = [float(row["p_yes"]) for row in evaluation]
    calibrated = [
        float(p) for p in estimator.predict_proba(
            [[_logit(prob)] for prob in incumbent],
        )[:, 1]
    ]
    labels = [int(row["actual_side"] == "ABOVE") for row in evaluation]
    report["incumbent"] = _proper_metrics(labels, incumbent)
    report["candidate"] = _proper_metrics(labels, calibrated)
    report["incumbent_69_gate"] = _gate(labels, incumbent)
    report["candidate_69_gate"] = _gate(labels, calibrated)
    report["calibration_slope"] = slope
    report["calibration_intercept"] = float(estimator.intercept_[0])
    report["brier_improvement"] = report["incumbent"]["brier"] - report["candidate"]["brier"]
    report["log_loss_improvement"] = report["incumbent"]["log_loss"] - report["candidate"]["log_loss"]
    matched = [
        (labels[i], incumbent[i], calibrated[i], quote)
        for i, row in enumerate(evaluation)
        if (quote := _quote_midpoint(row)) is not None
    ]
    report["matched_kalshi_midpoint_markets"] = len(matched)
    if matched:
        y = [item[0] for item in matched]
        report["market_midpoint"] = _proper_metrics(y, [item[3] for item in matched])
        report["incumbent_matched_market"] = _proper_metrics(y, [item[1] for item in matched])
        report["candidate_matched_market"] = _proper_metrics(y, [item[2] for item in matched])
    report["status"] = "RETROSPECTIVE_SHADOW_ONLY"
    return report


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last-markets", type=int, default=200)
    args = parser.parse_args(argv)
    root = BootstrapSettings().bootstrap_dir
    history = read_final_history(root)["forecasts"]
    replay = replay_eight_minute_history(
        root, history, max_markets=args.last_markets, include_replay_rows=True,
    )
    shadow = assess_480_shadow_calibration(replay["replay_rows"])
    shadow["replay_markets_with_on_time_observation"] = replay["markets_with_on_time_observation"]
    shadow["replay_ambiguous_earliest_observations"] = replay["ambiguous_earliest_observations"]
    shadow["replay_unhealthy_or_invalid_observations"] = replay["unhealthy_or_invalid_observations"]
    print(json.dumps(shadow, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
