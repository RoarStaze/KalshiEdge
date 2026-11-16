"""No-training, read-only signal conflict audit at matched forecast coverage.

No change to trained weights, live probability, 63% gate or AutoTrade.
Candidate selection rules are *predeclared* and use ONLY checkpoint-time data.
Paired historical outcomes are used to SCORE, NEVER to choose markets.

Important: this is exploratory ranking analysis on previously inspected data.
A rule that appears to improve accuracy here is not an independently validated
improvement, nor is a below-live-gate 'rescue' automatically safe to deploy.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .config import BootstrapSettings
from .final_forecast import COIN_FLIP_MAX_RISK_Z
from .final_history import _forecasts
from .forecast_policy_audit_480 import _GATE_REASONS
from .model_multisignal_comparison_480 import (
    THRESHOLDS_PERCENT,
    experimental_multisignal_probability,
)
from .signal_fusion_480 import FEATURE_NAMES, _Example, _verified_examples

AUDIT_POLICY = "read-only-no-fit-matched-coverage-error-audit-v1"
_FAMILIES = (
    "brti_strike_side",
    "candle_consensus",
    "btc_aggressor_flow",
    "kalshi_taker_flow",
)
_INDEX = {name: i for i, name in enumerate(FEATURE_NAMES)}


def classify_signal_conflicts(example: _Example) -> dict[str, bool]:
    """Fixed, unweighted signal-family disagreement with the *model side*.

    Raw BRTI risk-z sign is defined at the historical 480s checkpoint.
    15s and 60s candles comprise one family (not two independent votes).
    Aggregate signed BTC flow and Kalshi contract flow comprise separate
    venue-specific families. This is not a calibrated error probability.
    """
    p, x = example.probability, example.vector
    if (type(p) not in (float, int) or not math.isfinite(p)
            or not 0 <= p <= 1 or len(x) != len(FEATURE_NAMES)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in x)):
        raise ValueError("invalid causal model/signal checkpoint")
    direction = 1 if p >= 0.5 else -1
    z = x[_INDEX["brti_signed_risk_z"]]
    r15 = x[_INDEX["btc_return_15s_bps"]]
    r60 = x[_INDEX["btc_return_60s_bps"]]
    btc = x[_INDEX["btc_taker_buy_imbalance_60s"]]
    kalshi = x[_INDEX["kalshi_yes_taker_imbalance_60s"]]
    return {
        "brti_strike_side": z * direction < 0,
        "candle_consensus": r15 * direction < 0 and r60 * direction < 0,
        "btc_aggressor_flow": btc * direction < 0,
        "kalshi_taker_flow": kalshi * direction < 0,
    }


def _scored(
    indexes: set[int],
    baseline: set[int],
    probabilities: list[float],
    labels: list[int],
    eligible_gates: list[float],
    threshold: int,
) -> dict[str, Any]:
    k = len(indexes)
    correct = sum(
        (probabilities[i] >= .5) == bool(labels[i]) for i in indexes
    )
    total = len(probabilities)
    baseline_correct = sum(
        (probabilities[i] >= .5) == bool(labels[i]) for i in baseline
    )
    return {
        "qualified": k,
        "skipped": total - k,
        "correct": correct,
        "incorrect": k - correct,
        "accuracy": correct / k if k else None,
        "coverage": k / total if total else 0.,
        "replacements": len(indexes - baseline),
        "selected_below_original_gate": sum(
            eligible_gates[i] <= threshold / 100. for i in indexes
        ),
        "delta_correct_vs_gate": correct - baseline_correct,
        "delta_accuracy_percentage_points_vs_gate": (
            100. * (correct - baseline_correct) / k if k else None
        ),
    }


def evaluate_error_guard_audit(
    root: Path, *, model_hash: str,
) -> dict[str, Any]:
    """Measure model + signal *selection*, preserving base model probability.

    Each confidence gate sets a fixed count K based on the CURRENT model's
    original qualifying markets. Both candidate rankings select exactly K
    of the same flip-eligible 480s records, so coverage is identical.
    A candidate may substitute lower-than-gate model forecasts: explicitly
    report this nondeployable behavior instead of silently bypassing live 63%.
    """
    root=Path(root).resolve()
    if (not isinstance(model_hash, str) or len(model_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in model_hash)):
        raise ValueError("a single, valid model SHA-256 is required")
    examples, counts = _verified_examples(root, model_hash)
    permitted = {
        record.market_ticker for record in _forecasts(root)
        if getattr(record, "model_hash", model_hash) == model_hash
        and record.reason in _GATE_REASONS
    }
    rows: list[_Example] = []
    combined: list[float] = []
    conflicts: list[dict[str, bool]] = []
    excluded = {
        "missing_or_invalid_checkpoint_or_settlement": counts.get("missing_or_invalid", 0),
        "non_probability_skip": 0,
        "invalid_signal": 0,
        "recorded_brti_flip_veto": 0,
    }
    seen: set[str] = set()
    for item in examples:
        if item.ticker in seen:
            excluded["invalid_signal"] += 1
            continue
        seen.add(item.ticker)
        if item.ticker not in permitted:
            excluded["non_probability_skip"] += 1
            continue
        if (item.actual_yes not in (0, 1)
                or type(item.risk_z) not in (int, float)
                or not math.isfinite(item.risk_z)
                or not 0 <= item.risk_z <= 100):
            excluded["invalid_signal"] += 1
            continue
        try:
            family = classify_signal_conflicts(item)
            adjusted = experimental_multisignal_probability(
                item.probability, item.vector,
            )
        except (ValueError, TypeError):
            excluded["invalid_signal"] += 1
            continue
        rows.append(item)
        conflicts.append(family)
        combined.append(adjusted)
        if item.risk_z <= COIN_FLIP_MAX_RISK_Z:
            excluded["recorded_brti_flip_veto"] += 1

    p = [r.probability for r in rows]
    labels = [r.actual_yes for r in rows]
    side_confidence = [max(v, 1. - v) for v in p]
    eligible = [i for i, r in enumerate(rows) if r.risk_z > COIN_FLIP_MAX_RISK_Z]
    conflict_counts = [sum(int(x) for x in f.values()) for f in conflicts]
    family_counts: dict[str, dict[str, Any]] = {}
    for name in _FAMILIES:
        flagged = [
            i for i, flags in enumerate(conflicts) if flags[name]
        ]
        unflagged = [
            i for i, flags in enumerate(conflicts) if not flags[name]
        ]
        def mistakes(indices: list[int]) -> int:
            return sum(
                (p[i] >= .5) != bool(labels[i]) for i in indices
            )
        bad_flagged = mistakes(flagged)
        bad_unflagged = mistakes(unflagged)
        family_counts[name] = {
            "flagged": len(flagged),
            "flagged_wrong": bad_flagged,
            "flagged_error_rate": (
                bad_flagged / len(flagged) if flagged else None
            ),
            "unflagged": len(unflagged),
            "unflagged_wrong": bad_unflagged,
            "unflagged_error_rate": (
                bad_unflagged / len(unflagged) if unflagged else None
            ),
        }

    # Ranking never uses labels or settlements. The model is retained as
    # direction and tie-breaker for the conflict-priority selector.
    risk_rank = sorted(
        eligible, key=lambda i: (
            conflict_counts[i], -side_confidence[i], rows[i].ticker
        ),
    )
    # Matched-coverage counterfactual for the PREVIOUSLY TESTED 75/25 blend:
    # only its ranking is used; no change to model-side decision probability.
    combined_rank = sorted(
        eligible, key=lambda i: (
            -max(combined[i], 1.-combined[i]),
            -side_confidence[i], rows[i].ticker,
        ),
    )

    thresholds: list[dict[str, Any]] = []
    for threshold in THRESHOLDS_PERCENT:
        baseline: set[int] = {
            i for i in eligible if side_confidence[i] > threshold / 100.
        }
        k = len(baseline)
        selected_conflict = set(risk_rank[:k])
        selected_blend = set(combined_rank[:k])
        entry={
            "threshold_percent": threshold,
            "original_gate": _scored(
                baseline, baseline, p, labels, side_confidence, threshold,
            ),
            "conflict_priority": _scored(
                selected_conflict, baseline, p, labels, side_confidence, threshold,
            ),
            "existing_blend_priority": _scored(
                selected_blend, baseline, p, labels, side_confidence, threshold,
            ),
        }
        if any(entry[name]["qualified"] != k
               for name in ("conflict_priority", "existing_blend_priority")):
            raise ValueError("matched-coverage selection violated")
        thresholds.append(entry)

    return {
        "status": "DESCRIPTIVE_ONLY" if rows else "NO_VERIFIED_PAIRED_EVIDENCE",
        "policy": AUDIT_POLICY,
        "matched_settled_markets": len(rows),
        "model_hash": model_hash,
        "evidence_counts": counts,
        "excluded": excluded,
        "family_error_counts": family_counts,
        "thresholds": thresholds,
        "conflict_priority_rule": (
            "fewest BRTI/candle-consensus/BTC-flow/Kalshi-flow model "
            "disagreements, then descending original model side probability; "
            "no model probability changed and no label used to rank"
        ),
        "existing_blend_priority_rule": (
            "descending confidence of the previously rejected fixed 75/25 "
            "blend, original model side and original model probability retained"
        ),
        "underlying_model_probability_modified": False,
        "experimental_probabilities_used_for_live": False,
        "model_retraining": False,
        "live_gate_changed": False,
        "trades_executed": False,
        "limitation": (
            "DESCRIPTIVE ONLY. Same previously inspected outcomes, not "
            "independent validation. Equal coverage alone cannot establish "
            "improved probability quality. Rankings can select markets "
            "below the live directional gate, which MUST NOT be deployed. "
            "Conflicts are fixed uncalibrated directional rules, not learned "
            "probabilities. Selecting a policy after seeing this report "
            "creates data-snooping bias. No fees, fills or profitability "
            "are measured. Forward untouched settlements are necessary "
            "before any change to live trading."
        ),
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value*100:.2f}%"


def _print_table(result: dict[str, Any]) -> None:
    print("MODEL + MULTISIGNAL CONFLICTS | MATCHED COVERAGE 480s AUDIT")
    print(
        "Status:", result["status"],
        "| Paired markets:", result["matched_settled_markets"],
    )
    print("Model hash:", result["model_hash"])
    print(
        "Each strategy selects exactly as many markets as the unchanged model "
        "at that gate; recorded BRTI flip veto always applies."
    )
    print()
    print(
        f'{"Gate":>5} {"Selection":<24} {"Qualified":>9} {"Skipped":>7} '
        f'{"Correct":>7} {"Wrong":>6} {"Accuracy":>9} '
        f'{"Swaps":>6} {"Below gate":>10}'
    )
    for row in result["thresholds"]:
        gate = row["threshold_percent"]
        for name in ("original_gate", "conflict_priority", "existing_blend_priority"):
            r=row[name]
            print(
                f'{gate:>4}% {name:<24} {r["qualified"]:>9} {r["skipped"]:>7} '
                f'{r["correct"]:>7} {r["incorrect"]:>6} '
                f'{_pct(r["accuracy"]):>9} {r["replacements"]:>6} '
                f'{r["selected_below_original_gate"]:>10}'
            )
    print()
    print("Model error vs individual conflict families (descriptive):")
    for name, r in result["family_error_counts"].items():
        print(
            f'  {name}: flagged={r["flagged"]}, '
            f'flagged_error={_pct(r["flagged_error_rate"])}, '
            f'unflagged_error={_pct(r["unflagged_error_rate"])}'
        )
    print("Excluded:", result["excluded"])
    print("DESCRIPTIVE ONLY; no model training, live change or trade.")
    print(result["limitation"])


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--model-hash",default=None,
                        help="Single active or saved historical model SHA-256")
    parser.add_argument("--json",action="store_true",help="Machine-readable report")
    args=parser.parse_args(argv)
    sha=args.model_hash
    if sha is None:
        from .live import load_default_bundle
        try:
            sha=load_default_bundle(args.root).bundle_sha256
        except (ValueError,OSError,RuntimeError) as exc:
            parser.error(f"Active model hash unavailable: {exc}")
    try:
        report=evaluate_error_guard_audit(args.root,model_hash=sha)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
    else:
        _print_table(report)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
