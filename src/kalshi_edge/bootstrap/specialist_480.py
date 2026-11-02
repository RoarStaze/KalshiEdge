"""Eight-minute-only logistic specialist: read-only, never deploys weights.

This module reads the provenance-verified historical feature dataset and
evaluates ONE fixed candidate on later calibration markets, always before
the existing reserved lockbox. It does not overwrite, serialize, promote,
or load a live AutoTrade model, and never accesses exchange credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .metrics import probability_metrics
from .paired_gate_480 import summarize_gate_overlap
from .types import FeatureRow

CHECKPOINT_SECONDS = 480
CHECKPOINT_TOLERANCE_SECONDS = 1.0
MIN_TRAIN_MARKETS = 80
MIN_VALIDATION_MARKETS = 20
MIN_CONFIDENCE = .69
FEATURES = (
    "btc_normalized_distance",
    "btc_realized_vol_60s",
    "btc_realized_vol_300s",
    "btc_return_60s",
    "kalshi_mid",
    "kalshi_spread",
)
NS = 1_000_000_000


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _eligible_features(row: FeatureRow) -> str | None:
    features = row.features
    if any(name not in features or not _finite_number(features[name]) for name in FEATURES):
        return "missing_or_invalid_feature"
    required = (
        "kalshi_quote_available", "kalshi_yes_bid", "kalshi_yes_ask",
        "kalshi_quote_staleness_seconds", "btc_return_60s_available",
    )
    if any(name not in features or not _finite_number(features[name]) for name in required):
        return "missing_or_invalid_feature"
    bid, ask, mid = (
        features["kalshi_yes_bid"], features["kalshi_yes_ask"], features["kalshi_mid"],
    )
    if not (features["kalshi_quote_available"] >= 1.0
            and 0 < bid < ask < 1
            and abs(mid - (bid + ask) / 2) <= 1e-4
            and 0 <= features["kalshi_quote_staleness_seconds"] <= 120.0):
        return "invalid_or_stale_quote"
    if not (features["btc_return_60s_available"] >= 1.0
            and features["btc_realized_vol_60s"] > 0
            and features["btc_realized_vol_300s"] > 0):
        return "missing_or_invalid_btc_evidence"
    sources = row.source_max_ts_ns
    # No future sources and no absent sources used by this candidate.
    btc = sources.get("binance")
    kalshi = sources.get("kalshi_candles")
    ts = row.checkpoint_ts_ns
    if (type(ts) is not int or ts <= 0
            or type(btc) is not int or type(kalshi) is not int
            or not 0 < btc <= ts or not 0 < kalshi <= ts
            or ts - btc > 5 * NS or ts - kalshi > 120 * NS):
        return "future_or_missing_source"
    return None


def select_eight_minute_rows(
    rows: Sequence[FeatureRow],
) -> tuple[list[FeatureRow], dict[str, int]]:
    """One market = one true 480s observation, never a 240s proxy.

    Duplicate checkpoint observations of a ticker are all excluded.
    A normal 240s row in the same market is *not* a duplicate 480s row.
    """
    counts: Counter[str] = Counter()
    chosen: list[FeatureRow] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, FeatureRow):
            reasons["invalid_row"] += 1
            continue
        remaining = row.features.get("seconds_remaining")
        if (not _finite_number(remaining)
                or abs(remaining - CHECKPOINT_SECONDS) > CHECKPOINT_TOLERANCE_SECONDS):
            reasons["wrong_checkpoint"] += 1
            continue
        counts[row.market_ticker] += 1
        chosen.append(row)

    accepted: list[FeatureRow] = []
    for row in chosen:
        if counts[row.market_ticker] != 1:
            reasons["duplicate_rows"] += 1
            continue
        reason = _eligible_features(row)
        if reason is not None:
            reasons[reason] += 1
            continue
        accepted.append(row)
    return sorted(accepted, key=lambda r: (r.checkpoint_ts_ns, r.market_ticker)), dict(reasons)


def _status_report(status: str, train: list[FeatureRow], valid: list[FeatureRow],
                   exclusions: dict[str, dict[str, int]]) -> dict[str, Any]:
    return {
        "report": "KXBTC15M_VERIFIED_PARQUET_480S_SPECIALIST_V1",
        "status": status,
        "checkpoint_seconds": CHECKPOINT_SECONDS,
        "features": list(FEATURES),
        "fit_markets": len(train),
        "validation_markets": len(valid),
        "train_exclusions": exclusions["development"],
        "validation_exclusions": exclusions["calibration"],
        "last_fit_ticker": train[-1].market_ticker if train else None,
        "first_validation_ticker": valid[0].market_ticker if valid else None,
        "fit_last_ns": train[-1].checkpoint_ts_ns if train else None,
        "eval_first_ns": valid[0].checkpoint_ts_ns if valid else None,
        "candidate": None,
        "kalshi_midpoint": None,
        "candidate_69_gate": None,
        "kalshi_69_gate": None,
        "qualified_69_gate_verified_profit": None,
        "model_saved": False,
        "can_promote": False,
        "independent_lockbox": False,
        "note": (
            "Research-only: this experiment fits on VERIFIED development rows "
            "and evaluates on LATER calibration rows. That calibration period "
            "is NOT an untouched independent lockbox; model selection and "
            "hyperparameter tuning must not use its labels. "
            "NO new weights are saved, promoted or used for live trading. "
            "Historical accuracy/coverage does not establish an after-fee edge."
        ),
    }


def _gate(labels: list[int], probabilities: list[float]) -> dict[str, Any]:
    positions = [
        i for i, value in enumerate(probabilities)
        if 2 * abs(value - .5) >= MIN_CONFIDENCE
    ]
    wins = sum(int((probabilities[i] >= .5) == bool(labels[i])) for i in positions)
    return {
        "eligible_markets": len(positions),
        "correct": wins,
        "accuracy": wins / len(positions) if positions else None,
        "coverage": len(positions) / len(probabilities),
        "minimum_confidence": MIN_CONFIDENCE,
        "note": "Hypothetical confidence-qualified forecasts, not fills, trades or realized P/L.",
    }


def evaluate_eight_minute_specialist(
    development_rows: Sequence[FeatureRow],
    calibration_rows: Sequence[FeatureRow],
) -> dict[str, Any]:
    """Train only on historical development rows, validate only on later rows."""
    train, train_ex = select_eight_minute_rows(development_rows)
    valid, valid_ex = select_eight_minute_rows(calibration_rows)
    report = _status_report(
        "INSUFFICIENT_TRAIN_MARKETS", train, valid,
        {"development": train_ex, "calibration": valid_ex},
    )
    # Diagnose leakage BEFORE sample-size checks: do not conceal a reversed
    # or overlapping evaluation period behind a smaller training count.
    # Market groups must be disjoint and a whole BTC15m round must separate
    # the final training observation from the first validation observation.
    used_markets = {r.market_ticker for r in train}
    if (train and valid and (
            used_markets.intersection(r.market_ticker for r in valid)
            or {r.split_group_id for r in train}.intersection(
                r.split_group_id for r in valid
            )
            or train[-1].checkpoint_ts_ns + 900 * NS > valid[0].checkpoint_ts_ns)):
        report["status"] = "LEAKAGE_OR_OVERLAPPING_PERIODS"
        return report
    if len(train) < MIN_TRAIN_MARKETS:
        return report
    if len(valid) < MIN_VALIDATION_MARKETS:
        report["status"] = "INSUFFICIENT_VALIDATION_MARKETS"
        return report

    labels_train = [r.label_yes for r in train]
    if len(set(labels_train)) != 2:
        report["status"] = "SINGLE_CLASS_TRAIN_SET"
        return report

    # No adaptive search on validation labels: freeze C and the six features.
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    scaler_and_model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.15, solver="lbfgs", max_iter=1000, random_state=0,
        ),
    )
    x_train = [[r.features[name] for name in FEATURES] for r in train]
    x_eval = [[r.features[name] for name in FEATURES] for r in valid]
    scaler_and_model.fit(x_train, labels_train)
    candidate = [float(p) for p in scaler_and_model.predict_proba(x_eval)[:, 1]]
    market = [float(r.features["kalshi_mid"]) for r in valid]
    labels_eval = [r.label_yes for r in valid]
    cscore = probability_metrics(labels_eval, candidate).model_dump(mode="json")
    mscore = probability_metrics(labels_eval, market).model_dump(mode="json")
    candidate_gate = _gate(labels_eval, candidate)
    market_gate = _gate(labels_eval, market)
    paired_gate = summarize_gate_overlap(labels_eval, candidate, market)
    report.update(
        status="RESEARCH_VALIDATION_ONLY",
        candidate={"markets": len(valid), **cscore},
        kalshi_midpoint={"markets": len(valid), **mscore},
        candidate_69_gate=candidate_gate,
        kalshi_69_gate=market_gate,
        candidate_vs_kalshi_69_overlap=paired_gate,
        joint_objective_against_kalshi_retrospectively=bool(
            cscore["brier"] < mscore["brier"]
            and cscore["log_loss"] < mscore["log_loss"]
            and cscore["accuracy"] > mscore["accuracy"]
            and candidate_gate["eligible_markets"] > market_gate["eligible_markets"]
            and candidate_gate["accuracy"] is not None
            and market_gate["accuracy"] is not None
            and candidate_gate["accuracy"] >= market_gate["accuracy"]
        ),
        brier_candidate_minus_market=cscore["brier"] - mscore["brier"],
        log_loss_candidate_minus_market=cscore["log_loss"] - mscore["log_loss"],
        comparison_same_market=True,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    from . import training

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir,
                        help="verified bootstrap data directory; no output files written")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        context = training._verified_context(root)
        prior = training._read_rows_before_lockbox(context)
        development = set(context.plan.development_groups)
        calibration = set(context.plan.calibration_groups)
        train_rows = [r for r in prior if training._group(r) in development]
        validation_rows = [r for r in prior if training._group(r) in calibration]
        report = evaluate_eight_minute_specialist(train_rows, validation_rows)
        report["verified_feature_dataset_sha256"] = context.input_hashes["derived/features.parquet"]
        report["reserved_lockbox_opened"] = False
        report["partition"] = "VERIFIED_PRELOCKBOX_DEVELOPMENT_THEN_CALIBRATION"
    except (OSError, ValueError, training.TrainingError) as exc:
        report = {
            "report": "KXBTC15M_VERIFIED_PARQUET_480S_SPECIALIST_V1",
            "status": "DATASET_UNAVAILABLE_OR_UNVERIFIED",
            "error": str(exc),
            "model_saved": False,
            "can_promote": False,
            "reserved_lockbox_opened": False,
        }
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
