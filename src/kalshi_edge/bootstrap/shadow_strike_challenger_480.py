"""Offline eight-minute multi-signal challenger: NO live trade or model changes.

Tests one *fixed* low-capacity, regularized candidate on archived, same-tick
BRTI/strike, Kalshi quotes and incumbent probability; scores on later labels.
Historical evidence has already been inspected and is NOT an independent
lockbox. Nothing in this module can write a model bundle or place an order.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
import math
from typing import Any, Mapping, Sequence

from .shadow_calibration_480 import (
    _gate, _logit, _proper_metrics, _quote_midpoint, _valid,
)
from .shadow_eight_minute import replay_eight_minute_history

TRAIN_MARKETS = 60
MIN_TEST_MARKETS = 20
MAX_BRTI_STALENESS_NS = 5_000_000_000
BRTI_DISTANCE_BPS_UNIT = 100.0
DISTANCE_CLIP = 5.0
MIN_CONFIDENCE = .69
FEATURE_NAMES = [
    "incumbent_log_odds",
    "kalshi_midpoint_log_odds",
    "brti_to_strike_distance_100bps",
]


def _numbers(row: Mapping[str, Any]) -> list[float] | None:
    if not _valid(row):
        return None
    p = row["p_yes"]
    quote = _quote_midpoint(row)
    brti, strike = row.get("brti_value"), row.get("strike")
    source, observed = row.get("brti_source_ts_ns"), row.get("recorded_prediction_ts_ns")
    if (quote is None or not all(
            type(x) in (float, int) and math.isfinite(x) and x > 0
            for x in (brti, strike)
        ) or type(source) is not int or type(observed) is not int
        or source <= 0
        or not 0 <= observed - source <= MAX_BRTI_STALENESS_NS):
        return None
    # This is signed distance at the archived checkpoint, *not* the
    # historical future settlement value. Fixed normalization, no fit.
    signed_distance = ((brti / strike) - 1.0) * 10_000.0 / BRTI_DISTANCE_BPS_UNIT
    if not math.isfinite(signed_distance):
        return None
    signed_distance = min(DISTANCE_CLIP, max(-DISTANCE_CLIP, signed_distance))
    return [_logit(float(p)), _logit(quote), signed_distance]


def evaluate_strike_challenger(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Strict 60/20 chronological retrospective evaluation, never deployment."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("expected archived eight-minute observations")

    # Exclude *all* repeated ticker rows, rather than choosing the best
    # version or whichever row has more convenient complete fields.
    raw_tickers = [
        row.get("market_ticker")
        for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("market_ticker"), str)
        and row["market_ticker"]
    ]
    counts = Counter(raw_tickers)
    duplicates = sum(n for n in counts.values() if n > 1)
    valid = []
    unusable = 0
    for row in rows:
        if not isinstance(row, Mapping):
            unusable += 1
            continue
        ticker = row.get("market_ticker")
        if isinstance(ticker, str) and ticker and counts[ticker] > 1:
            continue
        vector = _numbers(row)
        if vector is None:
            unusable += 1
            continue
        valid.append((row, vector))

    valid.sort(key=lambda item: (item[0]["scheduled_checkpoint_ts_ns"], item[0]["market_ticker"]))
    model_hashes = dict(sorted(Counter(item[0]["model_hash"] for item in valid).items()))
    result: dict[str, Any] = {
        "report": "KXBTC15M_480S_BRTI_STRIKE_KALSHI_CHALLENGER_V1",
        "status": "INSUFFICIENT_MARKETS",
        "source": "ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS",
        "eligible_markets": len(valid),
        "total_replay_rows": len(rows),
        "invalid_or_unusable_records": unusable,
        "duplicate_records_excluded": duplicates,
        "model_hash_counts": model_hashes,
        "features": FEATURE_NAMES,
        "training_markets": 0,
        "evaluation_markets": 0,
        "train_last_market": None,
        "eval_first_market": None,
        "training_precedes_evaluation": False,
        "incumbent": None, "candidate": None,
        "market_midpoint": None,
        "incumbent_69_gate": None,
        "candidate_69_gate": None,
        "joint_target_met_retrospectively": False,
        "model_promotion": False,
        "live_orders": False,
        "independent_validation": False,
        "warning": (
            "Retrospective and repeatedly inspected historical observations "
            "are NOT independent validation. A better, more-confident shadow "
            "forecast is not a verified live accuracy increase or net-profit "
            "improvement. The Kalshi midpoint is not an executable price. "
            "No live model, AutoTrade gates, stake size or orders are changed."
        ),
    }

    if len(model_hashes) > 1:
        result["status"] = "MIXED_MODEL_HASHES"
        return result
    if len(valid) < TRAIN_MARKETS + MIN_TEST_MARKETS:
        return result

    train, evaluation = valid[:TRAIN_MARKETS], valid[TRAIN_MARKETS:]
    result["training_markets"] = len(train)
    result["evaluation_markets"] = len(evaluation)
    result["train_last_market"] = train[-1][0]["market_ticker"]
    result["eval_first_market"] = evaluation[0][0]["market_ticker"]
    result["training_precedes_evaluation"] = (
        train[-1][0]["scheduled_checkpoint_ts_ns"]
        < evaluation[0][0]["scheduled_checkpoint_ts_ns"]
    )
    # Equal timestamps in adjacent markets may represent duplicated market
    # windows. Do not train/evaluate across an ambiguous time boundary.
    if not result["training_precedes_evaluation"]:
        result["status"] = "AMBIGUOUS_CHRONOLOGY"
        return result

    y_train = [int(row["actual_side"] == "ABOVE") for row, _x in train]
    if len(set(y_train)) < 2:
        result["status"] = "INSUFFICIENT_TRAIN_LABEL_VARIATION"
        return result

    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression

    # Model selection and hyperparameters are fixed. No validation-label
    # tuning or adaptive threshold changes. Scaler fits TRAIN only.
    challenger = Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(
            solver="lbfgs", C=.20, max_iter=1000, random_state=0,
        )),
    ])
    challenger.fit([x for _row, x in train], y_train)
    p_candidate = [
        float(x) for x in challenger.predict_proba(
            [x for _row, x in evaluation],
        )[:, 1]
    ]
    p_incumbent = [float(row["p_yes"]) for row, _x in evaluation]
    p_market = [
        float(_quote_midpoint(row)) for row, _x in evaluation
    ]
    y_eval = [int(row["actual_side"] == "ABOVE") for row, _x in evaluation]

    result["incumbent"] = _proper_metrics(y_eval, p_incumbent)
    result["candidate"] = _proper_metrics(y_eval, p_candidate)
    result["market_midpoint"] = _proper_metrics(y_eval, p_market)
    original_gate = _gate(y_eval, p_incumbent)
    candidate_gate = _gate(y_eval, p_candidate)
    result["incumbent_69_gate"] = original_gate
    result["candidate_69_gate"] = candidate_gate
    result["brier_improvement"] = (
        result["incumbent"]["brier"] - result["candidate"]["brier"]
    )
    result["log_loss_improvement"] = (
        result["incumbent"]["log_loss"] - result["candidate"]["log_loss"]
    )
    result["joint_target_met_retrospectively"] = bool(
        result["brier_improvement"] > 0
        and result["log_loss_improvement"] > 0
        and result["candidate"]["accuracy"] > result["incumbent"]["accuracy"]
        and original_gate["accuracy"] is not None
        and candidate_gate["accuracy"] is not None
        and candidate_gate["eligible_markets"] > original_gate["eligible_markets"]
        and candidate_gate["accuracy"] > original_gate["accuracy"]
    )
    result["status"] = "RETROSPECTIVE_SHADOW_ONLY"
    return result


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last-markets", type=int, default=100)
    args = parser.parse_args(argv)
    root = BootstrapSettings().bootstrap_dir
    replay = replay_eight_minute_history(
        root, read_final_history(root)["forecasts"],
        max_markets=args.last_markets, include_replay_rows=True,
    )
    result = evaluate_strike_challenger(replay["replay_rows"])
    result["markets_missing_original_480s_observation"] = replay["markets_without_on_time_observation"]
    result["archive_health_failures"] = replay["unhealthy_or_invalid_observations"]
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
