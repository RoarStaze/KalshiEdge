"""Compare settled model reliability at SAME checkpoint horizons only.

Read-only diagnostics: this does not adjust confidence, thresholds, model
weights, promotion decisions, AutoTrade risk, or orders. Historic 240-second
forecasts must NEVER be presented as 480-second live trading evidence.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
import math
from typing import Any, Mapping, Sequence

from .forward_monitor import evaluate_forward_reliability

CURRENT_CHECKPOINT_SECONDS = 480
LEGACY_CHECKPOINT_SECONDS = 240
CHECKPOINTS = (LEGACY_CHECKPOINT_SECONDS, CURRENT_CHECKPOINT_SECONDS)
MIN_FINAL_CONFIDENCE = .69


def _checkpoint(row: Mapping[str, Any]) -> int | None:
    # Old immutable JSON files did not contain checkpoint_seconds; their
    # verified historical schedule was exactly 240 seconds before market close.
    value = row.get("checkpoint_seconds", LEGACY_CHECKPOINT_SECONDS)
    if type(value) is not int or value not in CHECKPOINTS:
        return None
    return value


def _confidence_mismatch(row: Mapping[str, Any]) -> bool:
    confidence = row.get("confidence")
    p = row.get("p_yes")
    other = row.get("p_no")
    if not all(type(x) in (int, float) and math.isfinite(x)
               for x in (confidence, p, other)):
        return False
    if not (0 <= p <= 1 and 0 <= other <= 1 and 0 <= confidence <= 1):
        return False
    if abs(p + other - 1) > 1e-6:
        return False
    return abs(confidence - 2 * abs(p - .5)) > 0.005


def _one_checkpoint(
    forecasts: list[Mapping[str, Any]], *, min_sample: int,
) -> dict[str, Any]:
    reasons = Counter(
        row.get("reason") if isinstance(row.get("reason"), str) and row.get("reason")
        else "UNKNOWN_SKIP"
        for row in forecasts if row.get("forecast") == "SKIP"
    )
    total = len(forecasts)
    directional = sum(row.get("forecast") in ("ABOVE", "BELOW") for row in forecasts)
    pending = sum(row.get("settlement_status") != "SETTLED" for row in forecasts)
    low = reasons.get("CONFIDENCE_BELOW_69_PERCENT", 0)
    reliability = evaluate_forward_reliability(forecasts, min_sample=min_sample)
    return {
        "total_forecast_records": total,
        "settled_records": total - pending,
        "pending_settlements": pending,
        "directional_forecasts": directional,
        "skipped_forecasts": sum(reasons.values()),
        "recorded_signal_fraction": directional / total if total else None,
        "low_confidence_skips": low,
        "feed_or_timing_skips": sum(reasons.values()) - low,
        "skip_reasons": dict(sorted(reasons.items())),
        "inconsistent_confidence_records": sum(_confidence_mismatch(row) for row in forecasts),
        "minimum_final_confidence": MIN_FINAL_CONFIDENCE,
        "minimum_model_side_probability_for_trading": (1 + MIN_FINAL_CONFIDENCE) / 2,
        "settled_eligible_markets": reliability["markets_scored"],
        "reliability": reliability,
        "warning": (
            "Research-only: recorded decisions include pending settlements and "
            "feed-health SKIPs, but proper scoring is strictly settled and excludes "
            "unscorable feed/timing failures. Directional fraction is NOT a fill rate "
            "or a trading profitability estimate."
        ),
    }


def evaluate_checkpoint_confidence(
    forecasts: Sequence[Mapping[str, Any]], *,
    min_sample: int = 100,
) -> dict[str, Any]:
    """Stratify old 240s vs active 480s confidence without selecting thresholds."""
    if type(min_sample) is not int or min_sample < 1:
        raise ValueError("min_sample must be a positive integer")
    if not isinstance(forecasts, (list, tuple)):
        raise ValueError("forecasts must be a list or tuple")
    by_horizon = {seconds: [] for seconds in CHECKPOINTS}
    invalid = 0
    for row in forecasts:
        if not isinstance(row, Mapping):
            invalid += 1
            continue
        horizon = _checkpoint(row)
        if horizon is None:
            invalid += 1
            continue
        by_horizon[horizon].append(row)
    grouped = {
        str(seconds): _one_checkpoint(by_horizon[seconds], min_sample=min_sample)
        for seconds in CHECKPOINTS
    }
    active = grouped[str(CURRENT_CHECKPOINT_SECONDS)]
    return {
        "report": "KXBTC15M_CHECKPOINT_STRATIFIED_CONFIDENCE_V1",
        "active_checkpoint_seconds": CURRENT_CHECKPOINT_SECONDS,
        "previous_checkpoint_seconds": LEGACY_CHECKPOINT_SECONDS,
        "by_checkpoint_seconds": grouped,
        "invalid_checkpoint_records": invalid,
        "active_policy_evidence": {
            "markets_scored": active["settled_eligible_markets"],
            "minimum_required_markets": min_sample,
            "has_sufficient_evidence": active["reliability"]["has_sufficient_evidence"],
            "valid_matched_market_quotes": active["reliability"]["market_midpoint_comparison"]["matched_markets"],
            "readiness": active["reliability"]["readiness"],
        },
        "policy_changed": True,
        "independent_validation": False,
        "warning": (
            "Research-only retrospective diagnostics, not a training or deployment "
            "rule. The old 240-second win rates and confidence do NOT validate "
            "the active 480-second policy. Even 100 settled 480-second markets "
            "are descriptive evidence, NOT independent model validation, "
            "a reason to inflate confidence, or proof of positive post-fee ROI. "
            "Never tune thresholds on this previously inspected sample."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-sample", type=int, default=100)
    args = parser.parse_args(argv)
    report = evaluate_checkpoint_confidence(
        read_final_history(BootstrapSettings().bootstrap_dir)["forecasts"],
        min_sample=args.min_sample,
    )
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
