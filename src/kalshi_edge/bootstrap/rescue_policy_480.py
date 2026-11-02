"""Frozen specialist low-confidence rescue: counterfactual research only.

The existing final forecast is authoritative. This *offline evaluator*
never runs in live inference, modifies a decision, trains a model or
communicates with Kalshi. It answers whether a frozen specialist could
have safely reduced only confidence-based abstentions, using forecasts
sealed before settlement and subsequently verified official outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from .final_forecast import FinalForecastRecord
from .final_history import _load_saved_settlement, SettlementIntegrityError
from .frozen_specialist_480 import FILENAME, load_frozen_specialist
from .paired_gate_480 import MIN_CONFIDENCE
from .prospective_shadow_480 import _gate_metric, _load_finals, _validated_shadow
from .shadow_live_480 import Shadow480Record

MIN_SETTLED_MARKETS = 80
LOW_CONFIDENCE_SKIP = "CONFIDENCE_BELOW_69_PERCENT"


def choose_rescue_forecast(
    final: FinalForecastRecord,
    shadow: Shadow480Record | None,
) -> tuple[str, str, float | None]:
    """Keep the incumbent forecast; rescue *only* an actual low-confidence SKIP.

    Shadow validity, freeze identity and file hash must first be established
    by _validated_shadow. Identity is rechecked here to make the policy
    fail-closed as a standalone pure function as well.
    """
    if final.forecast in ("ABOVE", "BELOW"):
        return final.forecast, "INCUMBENT", final.p_yes
    if (final.forecast != "SKIP"
            or final.reason != LOW_CONFIDENCE_SKIP
            or final.confidence is None
            or final.p_yes is None
            or not (0 <= final.confidence < MIN_CONFIDENCE)
            or abs(final.confidence - 2 * abs(final.p_yes - .5)) > 1e-6):
        return "SKIP", "ORIGINAL_SKIP", None
    if (shadow is None or shadow.status != "OK"
            or not shadow.gate_qualified or shadow.p_yes is None
            or shadow.market_ticker != final.market_ticker
            or shadow.scheduled_ts_ns != final.scheduled_ts_ns
            or shadow.prediction_ts_ns != final.prediction_ts_ns
            or shadow.incumbent_model_hash != final.model_hash
            or shadow.incumbent_p_yes != final.p_yes):
        return "SKIP", "ORIGINAL_SKIP", None
    return ("ABOVE" if shadow.p_yes >= .5 else "BELOW",
            "FROZEN_RESCUE", shadow.p_yes)


def evaluate_rescue_policy(root: Path, *, minimum_settled: int = MIN_SETTLED_MARKETS) -> dict[str, Any]:
    """Score fixed rescue policy on the exact future-after-freeze settlement set.

    Missing or corrupt frozen snapshots cannot be rescued, and cannot be
    silently removed from denominator. Settlement is read *after* the
    immutable forecasts are validated; it never enters policy selection.
    """
    if type(minimum_settled) is not int or minimum_settled < 1:
        raise ValueError("minimum_settled must be a positive integer")
    root = Path(root).resolve()
    frozen = load_frozen_specialist(root / FILENAME)
    finals, invalid_finals = _load_finals(root, frozen["frozen_at_ns"])

    counts: Counter[str] = Counter()
    incumbent_qualified = incumbent_correct = 0
    hybrid_qualified = hybrid_correct = 0
    rescue_count = rescue_correct = 0
    matched_snapshots = 0
    old_probabilities: list[tuple[int, float]] = []
    hybrid_probabilities: list[tuple[int, float]] = []
    missing_tickers: list[str] = []

    for final in finals:
        try:
            settlement = _load_saved_settlement(root, final)
        except SettlementIntegrityError:
            counts["invalid_settlements"] += 1
            continue
        if settlement is None:
            counts["pending_settlements"] += 1
            continue
        counts["settled_market_count"] += 1
        actual = settlement.actual_side

        incumbent_forecast = final.forecast
        if incumbent_forecast in ("ABOVE", "BELOW"):
            incumbent_qualified += 1
            incumbent_correct += int(incumbent_forecast == actual)
            if final.p_yes is not None:
                old_probabilities.append((int(actual == "ABOVE"), final.p_yes))

        shadow, issue = _validated_shadow(
            root, final, frozen["sha256"], frozen["frozen_at_ns"],
        )
        if issue is not None:
            if issue == "MISSING_SHADOW":
                counts["missing_shadow_records"] += 1
                missing_tickers.append(final.market_ticker)
            else:
                counts["invalid_shadow_records"] += 1
        else:
            matched_snapshots += 1
            if shadow.status == "SKIP":
                counts["explicit_shadow_skips"] += 1

        # This function sees no official outcome and cannot tune its
        # selection. The outcome is used solely in the following audit.
        side, source, chosen_probability = choose_rescue_forecast(final, shadow)
        if side in ("ABOVE", "BELOW"):
            hybrid_qualified += 1
            hybrid_correct += int(side == actual)
            if chosen_probability is not None:
                hybrid_probabilities.append((int(actual == "ABOVE"), chosen_probability))
        if source == "FROZEN_RESCUE":
            rescue_count += 1
            rescue_correct += int(side == actual)

    total = counts["settled_market_count"]
    original = _gate_metric(total, incumbent_qualified, incumbent_correct, old_probabilities)
    hybrid = _gate_metric(total, hybrid_qualified, hybrid_correct, hybrid_probabilities)
    return {
        "report": "KXBTC15M_480S_FIXED_FROZEN_CONFIDENCE_RESCUE_V1",
        "status": "DESCRIPTIVE_ONLY" if total >= minimum_settled else "INSUFFICIENT_SETTLED_MARKETS",
        "frozen_model_sha256": frozen["sha256"],
        "frozen_at_ns": frozen["frozen_at_ns"],
        "confidence_unchanged": MIN_CONFIDENCE,
        "minimum_required_settled_markets": minimum_settled,
        "final_480_after_freeze": len(finals),
        "settled_market_count": total,
        "pending_settlements": counts["pending_settlements"],
        "invalid_settlements": counts["invalid_settlements"],
        "invalid_final_records": invalid_finals,
        "matched_shadow_snapshots": matched_snapshots,
        "missing_shadow_records": counts["missing_shadow_records"],
        "invalid_shadow_records": counts["invalid_shadow_records"],
        "explicit_shadow_skips": counts["explicit_shadow_skips"],
        "missing_shadow_market_tickers": missing_tickers,
        "incumbent": original,
        "rescue_hybrid": hybrid,
        "rescues": {
            "markets": rescue_count,
            "correct": rescue_correct,
            "wrong": rescue_count - rescue_correct,
            "accuracy": rescue_correct / rescue_count if rescue_count else None,
        },
        "net_new_correct_forecasts": hybrid_correct - incumbent_correct,
        "net_new_qualifying_forecasts": hybrid_qualified - incumbent_qualified,
        "coverage_improvement": (hybrid["coverage"] - original["coverage"])
        if total else None,
        "qualified_accuracy_change": (
            hybrid["qualified_accuracy"] - original["qualified_accuracy"]
            if hybrid["qualified_accuracy"] is not None
            and original["qualified_accuracy"] is not None else None
        ),
        "ready_to_promote": False,
        "independent_validation_proven": False,
        "note": (
            "COUNTERFACTUAL read-only policy, not an actual executed strategy. "
            "Incumbent qualifying forecasts stay unchanged. Only original "
            "confidence-below-69-percent SKIPs with valid, matching, frozen "
            "specialist qualifying evidence can be hypothetically rescued. "
            "Missing/invalid shadows remain unqualified in the all-settled "
            "denominator. Training and live model remain unchanged. Small "
            "samples and selection effects limit accuracy conclusions. "
            "This does not prove profits after spreads, slippage or fees."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--minimum-settled", type=int, default=MIN_SETTLED_MARKETS)
    args = parser.parse_args(argv)
    try:
        result = evaluate_rescue_policy(args.root, minimum_settled=args.minimum_settled)
    except (OSError, ValueError) as exc:
        result = {"status": "EVALUATION_REFUSED", "error": str(exc), "ready_to_promote": False}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
