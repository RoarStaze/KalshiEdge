"""No-fit, read-only *paired* model-vs-multi-signal 480s threshold experiment.

An exploratory FIXED, explicitly UNCALIBRATED adjustment is calculated from
existing causal BRTI/flow/candle snapshots. The original promoted probability
is never altered, and no training, writes or trades occur.

Do NOT use this experiment as a production predictor or claim validated gains:
the named weights are research assumptions, not coefficients learned from
Kalshi history. Choosing a winner on these same outcomes is not an
independent test. The fitted fusion from signal_fusion_480 is NOT activated.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .config import BootstrapSettings
from .final_forecast import COIN_FLIP_MAX_RISK_Z
from .final_history import _forecasts
from .forecast_policy_audit_480 import _GATE_REASONS, SWEEP_THRESHOLDS_PERCENT
from .metrics import probability_metrics
from .signal_fusion_480 import FEATURE_NAMES, _verified_examples

THRESHOLDS_PERCENT = SWEEP_THRESHOLDS_PERCENT
EXPERIMENT_POLICY = "read-only-no-fit-model-multisignal-480-v1"

# Predeclared illustrative hypothesis; these are NOT fitted / calibrated.
# Different model components often share inputs; no independent signal votes
# or guarantees of accuracy follow from this convex mixture.
FIXED_BLEND = {
    "base_model": 0.75,
    "brti_reference": 0.15,
    "candle_reversal": 0.06,
    "signed_market_flow": 0.04,
}
_IDX = {name: index for index, name in enumerate(FEATURE_NAMES)}
if len(_IDX) != len(FEATURE_NAMES) or abs(sum(FIXED_BLEND.values()) - 1.0) > 1e-12:
    raise RuntimeError("invalid fixed experimental signal schema")


def experimental_multisignal_probability(
    base_p_yes: float, vector: Sequence[float],
) -> float:
    """Unfitted BRTI + flip-risk + candle/reversal + signed buy/sell overlay.

    NEVER called by the live predictor. No labels or settlement fields enter.
    The previously captured `signal_fusion_480.build_signal_vector` supplies
    the point-in-time verified, schema-checked input vector.
    """
    if (type(base_p_yes) not in (float, int)
            or not math.isfinite(base_p_yes) or not 0 <= base_p_yes <= 1):
        raise ValueError("invalid frozen base-model probability")
    if len(vector) != len(FEATURE_NAMES) or any(
        type(x) not in (float, int) or not math.isfinite(x) for x in vector
    ):
        raise ValueError("nonfinite or incomplete causal multi-signal vector")
    x = {name: float(vector[index]) for name, index in _IDX.items()}
    clipped_base = max(1e-5, min(1 - 1e-5, float(base_p_yes)))
    expected_logit = math.log(clipped_base / (1 - clipped_base))
    if abs(x["base_logit"] - expected_logit) > 1e-6:
        raise ValueError("multi-signal feature vector not bound to same base probability")
    if (abs(x["brti_signed_risk_z"]) > 10 or abs(x["brti_distance_bps"]) > 250
            or abs(x["brti_basis_bps"]) > 200
            or abs(x["btc_taker_buy_imbalance_60s"]) > 1
            or abs(x["kalshi_yes_taker_imbalance_60s"]) > 1
            or abs(x["signed_aggressor_volume_btc"]) > 500):
        raise ValueError("out-of-schema signal evidence")

    # Settlement-referenced strike distance normalized by causal BRTI
    # volatility, attenuated when BRTI/Binance simultaneously diverge.
    # This is a proxy, NOT an empirically calibrated P(BRTI settles ABOVE).
    signed_risk = x["brti_signed_risk_z"]
    basis_reliability = 1.0 / (1.0 + abs(x["brti_basis_bps"]) / 25.0)
    brti_probability = (
        0.5 + 0.45 * math.tanh(signed_risk / 1.5) * basis_reliability
    )

    # Signed recent candles and opposite-to-strike-side reversal pressure.
    # No future bars and no TradingView proprietary indicator API.
    candle_direction = math.tanh(
        0.5 * x["btc_return_15s_bps"] / 15.0
        + 0.5 * x["btc_return_60s_bps"] / 30.0
        - 0.3 * x["near_strike_reversal_bps"] / 15.0
    )
    candle_probability = 0.5 + 0.22 * candle_direction

    # Aggregate aggressor buys minus sells + Kalshi taker flow; not whales.
    signed_flow = math.tanh(
        0.65 * x["btc_taker_buy_imbalance_60s"]
        + 0.20 * math.tanh(x["signed_aggressor_volume_btc"] / 50.0)
        + 0.15 * x["kalshi_yes_taker_imbalance_60s"]
    )
    flow_probability = 0.5 + 0.22 * signed_flow

    adjusted = (
        FIXED_BLEND["base_model"] * float(base_p_yes)
        + FIXED_BLEND["brti_reference"] * brti_probability
        + FIXED_BLEND["candle_reversal"] * candle_probability
        + FIXED_BLEND["signed_market_flow"] * flow_probability
    )
    if not 0.0 <= adjusted <= 1.0 or not math.isfinite(adjusted):
        raise ValueError("experimental adjusted probability invalid")
    return adjusted


def _overall(probabilities: list[float], labels: list[int]) -> dict[str, Any]:
    if not probabilities:
        return {
            "count": 0, "brier": None, "log_loss": None, "ece": None,
            "all_market_directional_accuracy": None,
        }
    metrics = probability_metrics(labels, probabilities)
    return {
        "count": len(probabilities),
        "brier": metrics.brier,
        "log_loss": metrics.log_loss,
        "ece": metrics.ece,
        "all_market_directional_accuracy": metrics.accuracy,
    }


def _gate_row(
    probabilities: list[float], labels: list[int], risk_z: list[float], gate: int,
) -> dict[str, Any]:
    selected = [
        i for i, (p, risk) in enumerate(zip(probabilities, risk_z))
        if risk > COIN_FLIP_MAX_RISK_Z and max(p, 1-p) > gate / 100.
    ]
    correct = sum((probabilities[i] >= 0.5) == bool(labels[i]) for i in selected)
    qualified = len(selected)
    total = len(probabilities)
    return {
        "qualified": qualified, "skipped": total - qualified,
        "correct": correct, "incorrect": qualified - correct,
        "accuracy": correct / qualified if qualified else None,
        "coverage": qualified / total if total else 0.,
    }


def _add_incremental(
    rows: list[dict[str, Any]], predictor: str,
) -> None:
    for i, row in enumerate(rows):
        target = row[predictor]
        higher = rows[i + 1] if i + 1 < len(rows) else None
        if higher is None:
            target.update({
                "incremental_vs_higher_threshold_percent": None,
                "incremental_qualified": None,
                "incremental_correct": None,
                "incremental_incorrect": None,
                "incremental_accuracy": None,
            })
            continue
        high_stats = higher[predictor]
        added = target["qualified"] - high_stats["qualified"]
        added_correct = target["correct"] - high_stats["correct"]
        wrong = added - added_correct
        if min(added, added_correct, wrong) < 0:
            raise ValueError("non-nested probability thresholds")
        target.update({
            "incremental_vs_higher_threshold_percent": higher["threshold_percent"],
            "incremental_qualified": added,
            "incremental_correct": added_correct,
            "incremental_incorrect": wrong,
            "incremental_accuracy": added_correct / added if added else None,
        })


def evaluate_paired_comparison(
    root: Path, *, model_hash: str,
) -> dict[str, Any]:
    """Only hash-verified, time-locked and officially settled SAME-model rows.

    Reuses the existing strict fusion evidence loader to exclude unrecorded
    BRTI/flow/candle signals. No outcomes participate in signal generation;
    they are consulted only after both predictions have been calculated.
    """
    root = Path(root).resolve()
    if (not isinstance(model_hash, str) or len(model_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in model_hash)):
        raise ValueError("comparison requires a single SHA-256 model hash")
    examples, counts = _verified_examples(root, model_hash)
    # Same policy-health exclusions as the earlier gate-only audit, but only
    # within the smaller common cohort with all necessary causal signals.
    allowed = {
        record.market_ticker
        for record in _forecasts(root)
        if record.reason in _GATE_REASONS
    }
    model: list[float] = []
    multisignal: list[float] = []
    labels: list[int] = []
    risks: list[float] = []
    exclusions = {
        "missing_or_invalid_signal_or_settlement": counts.get("missing_or_invalid", 0),
        "non_probability_skip": 0,
        "invalid_or_missing_signal": 0,
    }
    seen = set()
    for row in examples:
        if row.ticker in seen:
            exclusions["invalid_or_missing_signal"] += 1
            continue
        seen.add(row.ticker)
        if row.ticker not in allowed:
            exclusions["non_probability_skip"] += 1
            continue
        if (row.actual_yes not in (0, 1)
                or not math.isfinite(row.risk_z)
                or row.risk_z < 0):
            exclusions["invalid_or_missing_signal"] += 1
            continue
        try:
            updated = experimental_multisignal_probability(row.probability, row.vector)
        except (TypeError, ValueError):
            exclusions["invalid_or_missing_signal"] += 1
            continue
        model.append(row.probability)
        multisignal.append(updated)
        labels.append(row.actual_yes)
        risks.append(row.risk_z)

    tables: list[dict[str, Any]] = []
    for gate in THRESHOLDS_PERCENT:
        base = _gate_row(model, labels, risks, gate)
        adjusted = _gate_row(multisignal, labels, risks, gate)
        tables.append({
            "threshold_percent": gate,
            "model": base, "multisignal": adjusted,
            "delta_multisignal_minus_model": {
                "qualified": adjusted["qualified"] - base["qualified"],
                "skipped": adjusted["skipped"] - base["skipped"],
                "correct": adjusted["correct"] - base["correct"],
                "incorrect": adjusted["incorrect"] - base["incorrect"],
                "accuracy_percentage_points": (
                    None if base["accuracy"] is None or adjusted["accuracy"] is None
                    else 100. * (adjusted["accuracy"] - base["accuracy"])
                ),
                "coverage_percentage_points": 100. * (
                    adjusted["coverage"] - base["coverage"]
                ),
            },
        })
    for name in ("model", "multisignal"):
        _add_incremental(tables, name)
    return {
        "status": "DESCRIPTIVE_ONLY" if model else "NO_VERIFIED_PAIRED_EVIDENCE",
        "policy": EXPERIMENT_POLICY,
        "model_hash": model_hash,
        "matched_settled_markets": len(model),
        "evidence_counts": counts,
        "excluded": exclusions,
        "signal_sources": [
            "BRTI signed volatility-normalized strike distance and BRTI-Binance basis",
            "BRTI near-strike flip-risk veto (identical both sides)",
            "15s/60s Binance candle momentum plus near-strike reversal",
            "60s aggressive BTC buy/sell and signed aggregate volume",
            "60s Kalshi taker-side flow",
        ],
        "fixed_heuristic_blend_NOT_CALIBRATED": FIXED_BLEND,
        "predictors": {
            "model": _overall(model, labels),
            "multisignal": _overall(multisignal, labels),
        },
        "direction_disagreements": sum(
            (a >= 0.5) != (b >= 0.5) for a, b in zip(model, multisignal)
        ),
        "mean_absolute_probability_change": (
            sum(abs(a-b) for a, b in zip(model, multisignal)) / len(model)
            if model else None
        ),
        "thresholds": tables,
        "model_retraining": False,
        "live_gate_changed": False,
        "trades_executed": False,
        "limitation": (
            "EXPLORATORY only: fixed uncalibrated signal weights chosen before "
            "scoring, not a fitted or deployed predictor. This is NOT independent "
            "validation or proof of better probabilities, profits or execution. "
            "The same historic settled rows are used across all eight thresholds, "
            "so selecting a winner on these results is subject to selection bias. "
            "No sample has been used for training, but overlapping original "
            "model training history is not independently ruled out."
        ),
    }


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100*x:.2f}%"


def _print_table(report: dict[str, Any]) -> None:
    print("MODEL vs MULTISIGNAL | eight 480s directional-probability gates")
    print("Status:", report["status"], "| Paired markets:", report["matched_settled_markets"])
    print("Model hash:", report["model_hash"])
    print("Threshold is STRICTLY GREATER; identical recorded BRTI flip veto.")
    print("Incremental accuracy: newly qualified vs next higher threshold.")
    print()
    print(f'{"Gate":>5} {"Predictor":<12} {"Qualified":>9} {"Skipped":>7} '
          f'{"Correct":>7} {"Wrong":>6} {"Accuracy":>9} {"Coverage":>9} '
          f'{"Incremental accuracy":>21}')
    for row in report["thresholds"]:
        gate = row["threshold_percent"]
        for name in ("model", "multisignal"):
            metrics = row[name]
            print(
                f'{gate:>4}% {name:<12} {metrics["qualified"]:>9} '
                f'{metrics["skipped"]:>7} {metrics["correct"]:>7} '
                f'{metrics["incorrect"]:>6} {_pct(metrics["accuracy"]):>9} '
                f'{_pct(metrics["coverage"]):>9} '
                f'{_pct(metrics["incremental_accuracy"]):>21}'
            )
    print()
    for name in ("model", "multisignal"):
        m = report["predictors"][name]
        print(
            f'{name}: Brier={m["brier"] if m["brier"] is not None else "-"} '
            f'LogLoss={m["log_loss"] if m["log_loss"] is not None else "-"} '
            f'ECE={m["ece"] if m["ece"] is not None else "-"}'
        )
    print("Excluded:", report["excluded"])
    print("DESCRIPTIVE ONLY; fixed uncalibrated experimental signal blend, not a model improvement.")
    print(report["limitation"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--model-hash", type=str, default=None,
                        help="Single historical model bundle SHA-256; defaults to active model")
    parser.add_argument("--json", action="store_true",
                        help="Structured paired comparison and metric deltas")
    args = parser.parse_args(argv)
    model_hash = args.model_hash
    if model_hash is None:
        from .live import load_default_bundle
        try:
            model_hash = load_default_bundle(args.root).bundle_sha256
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(f"Cannot identify currently promoted bundle: {exc}")
    try:
        report = evaluate_paired_comparison(args.root, model_hash=model_hash)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        _print_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
