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
from datetime import datetime, timezone
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
# Committed after the previously viewed 3-market evaluation, before the
# independent forward cohort. Fixed UTC boundary, never reset on restart.
POLICY_REGISTERED_AT_NS = int(
    datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000
)


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
    forward_total = forward_incumbent_qualified = forward_incumbent_correct = 0
    forward_hybrid_qualified = forward_hybrid_correct = 0
    forward_rescue_count = forward_rescue_correct = 0
    forward_original_probabilities: list[tuple[int, float]] = []
    forward_hybrid_probabilities: list[tuple[int, float]] = []

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

        # Exclude the already-observed early wins from genuine post-policy
        # validation. The policy was fixed at the registration timestamp,
        # not when official settlements were later read by this process.
        if final.scheduled_ts_ns > POLICY_REGISTERED_AT_NS:
            forward_total += 1
            if incumbent_forecast in ("ABOVE", "BELOW"):
                forward_incumbent_qualified += 1
                forward_incumbent_correct += int(incumbent_forecast == actual)
                if final.p_yes is not None:
                    forward_original_probabilities.append((int(actual == "ABOVE"), final.p_yes))
            if side in ("ABOVE", "BELOW"):
                forward_hybrid_qualified += 1
                forward_hybrid_correct += int(side == actual)
                if chosen_probability is not None:
                    forward_hybrid_probabilities.append(
                        (int(actual == "ABOVE"), chosen_probability)
                    )
            if source == "FROZEN_RESCUE":
                forward_rescue_count += 1
                forward_rescue_correct += int(side == actual)

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
        "forward_validation": {
            "policy_registered_at_ns": POLICY_REGISTERED_AT_NS,
            "settled_market_count": forward_total,
            "incumbent": _gate_metric(
                forward_total, forward_incumbent_qualified,
                forward_incumbent_correct, forward_original_probabilities,
            ),
            "rescue_hybrid": _gate_metric(
                forward_total, forward_hybrid_qualified,
                forward_hybrid_correct, forward_hybrid_probabilities,
            ),
            "rescues": {
                "markets": forward_rescue_count,
                "correct": forward_rescue_correct,
                "wrong": forward_rescue_count - forward_rescue_correct,
                "accuracy": (
                    forward_rescue_correct / forward_rescue_count
                    if forward_rescue_count else None
                ),
            },
            "ready_to_promote": False,
            "note": (
                "Only settled markets whose checkpoint occurs strictly "
                "after the fixed research-policy registration boundary. "
                "This cohort excludes the early observed rescue successes "
                "that motivated this policy; missing and invalid shadows "
                "remain in the all-settled denominator. It is not proof "
                "of an independent market edge or trade profitability."
            ),
        },
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
            "Earlier exploratory replays were used to select this policy. "
            "Use forward_validation for later outcomes, without optimizing "
            "the frozen 69-percent gate. This does not prove profits "
            "after spreads, slippage or fees."
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
