"""Read-only, fixed-bin Kalshi BTC15m final confidence reliability audit.

Never mixes old 240s markets with current 480s checkpoints. Never changes
models, thresholds, trading, or saved evidence. All labels are loaded only
from locally verified official Kalshi settlement records.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from .final_forecast import FinalForecastRecord
from .final_history import (
    SAFE_TICKER, SettlementIntegrityError, _close_ns, _load_saved_settlement,
)
from .rescue_policy_480 import POLICY_REGISTERED_AT_NS

# Fixed in code BEFORE inspection of subsequent 480-second forward labels.
BANDS: tuple[tuple[float, float, str], ...] = (
    (0.0, .2, "0%-<20%"),
    (.2, .4, "20%-<40%"),
    (.4, .6, "40%-<60%"),
    (.6, .69, "60%-<69%"),
    (.69, .8, "69%-<80%"),
    (.8, .9, "80%-<90%"),
    (.9, 1.0, "90%-100%"),
)
NS = 1_000_000_000
Z_95 = 1.959963984540054


def confidence_bin(confidence: float) -> str:
    if (type(confidence) not in (float, int)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1):
        raise ValueError("confidence must be finite in [0,1]")
    for low, high, name in BANDS:
        if low <= confidence < high or (high == 1.0 and confidence == 1):
            return name
    raise ValueError("confidence did not match a fixed band")


def wilson_interval(correct: int, total: int) -> tuple[float | None, float | None]:
    """Two-sided Wilson score 95% interval for a binary hit rate."""
    if (type(correct) is not int or type(total) is not int
            or total < 0 or not 0 <= correct <= total):
        raise ValueError("invalid binary success counts")
    if total == 0:
        return (None, None)
    p = correct / total
    z2 = Z_95 ** 2
    den = 1 + z2 / total
    center = (p + z2 / (2 * total)) / den
    radius = Z_95 * math.sqrt(
        p * (1-p) / total + z2 / (4 * total * total)
    ) / den
    return (max(0.0, center-radius), min(1.0, center+radius))


def _strict_finals(root: Path) -> tuple[list[FinalForecastRecord], int]:
    """Reject malformed paths and every member of any duplicate ticker."""
    good: dict[str, FinalForecastRecord] = {}
    occurrences: Counter[str] = Counter()
    invalid = 0
    for path in sorted((root / "final_forecasts").glob("*/*.json")):
        try:
            f = FinalForecastRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if not SAFE_TICKER.fullmatch(f.market_ticker):
                raise ValueError("invalid ticker")
            # Detect a second saved record even if its directory is wrong.
            occurrences[f.market_ticker] += 1
            day = datetime.fromtimestamp(_close_ns(f) / NS, timezone.utc).date().isoformat()
            if path.name != f"{f.market_ticker}.json" or path.parent.name != day:
                raise ValueError("forecast filename or UTC close date mismatch")
            good[f.market_ticker] = f
        except (ValueError, OSError, OverflowError):
            invalid += 1
    for ticker, n in occurrences.items():
        if n > 1:
            invalid += 1 if ticker in good else 0
            good.pop(ticker, None)
    return sorted(good.values(), key=lambda f: (f.scheduled_ts_ns, f.market_ticker)), invalid


def _cohort(final: FinalForecastRecord) -> str:
    if final.checkpoint_seconds == 240:
        return "legacy_240"
    return ("forward_480" if final.scheduled_ts_ns > POLICY_REGISTERED_AT_NS
            else "early_480")


def _probability_valid(final: FinalForecastRecord) -> bool:
    p, confidence = final.p_yes, final.confidence
    if (type(p) not in (int, float)
            or type(confidence) not in (int, float)
            or not math.isfinite(p)
            or not math.isfinite(confidence)
            or not 0 <= p <= 1 or not 0 <= confidence <= 1
            or abs(confidence - 2 * abs(p - .5)) > 1e-6):
        return False
    if final.p_no is not None and abs(final.p_no - (1-p)) > 1e-6:
        return False
    if final.forecast == "ABOVE":
        return p >= .5 and confidence >= .69
    if final.forecast == "BELOW":
        return p < .5 and confidence >= .69
    # A non-confidence or unhealthy SKIP is never given a pseudo-forecast.
    return (final.forecast == "SKIP"
            and final.reason == "CONFIDENCE_BELOW_69_PERCENT"
            and confidence < .69)


def _make_state() -> dict[str, Any]:
    return {
        "settled": 0, "pending": 0, "invalid_settlement": 0,
        "invalid_probability": 0,
        "issued": [0, 0],
        "skipped": [0, 0],
        "other_skips": Counter(),
        "bands": {name: {"issued": [0, 0], "skipped": [0, 0]}
                  for _, _, name in BANDS},
    }


def _stat(n: int, correct: int, *, skipped: bool = False) -> dict[str, Any]:
    lo, hi = wilson_interval(correct, n)
    result = {
        "markets": n,
        "incorrect": n - correct,
        "wilson_95_lower": lo,
        "wilson_95_upper": hi,
        "small_sample_under_30": n < 30,
    }
    if skipped:
        result["hypothetically_correct"] = correct
        result["hypothetical_directional_accuracy"] = correct/n if n else None
    else:
        result["correct"] = correct
        result["accuracy"] = correct/n if n else None
    return result


def _finalize(state: dict[str, Any]) -> dict[str, Any]:
    total = state["settled"]
    return {
        "settled_markets": total,
        "pending_settlements": state["pending"],
        "invalid_settlements": state["invalid_settlement"],
        "issued": _stat(*state["issued"]),
        "skips_with_valid_probabilities": _stat(*state["skipped"], skipped=True),
        "other_skip_reasons": dict(state["other_skips"]),
        "invalid_probability_records": state["invalid_probability"],
        "decision_coverage": state["issued"][0] / total if total else None,
        "bands": {
            name: {
                "total_markets": values["issued"][0] + values["skipped"][0],
                "issued": _stat(*values["issued"]),
                "skipped": _stat(*values["skipped"], skipped=True),
            }
            for name, values in state["bands"].items()
        },
        "note": (
            "Original issued accuracy uses the saved ABOVE/BELOW decision. "
            "Skipped-side accuracy is counterfactual only: higher recorded "
            "point-in-time P(YES)/P(NO), never an actual forecast or trade. "
            "No bin counts invalid confidence/probability or unhealthy SKIPs."
        ),
    }


def evaluate_confidence_reliability(root: Path) -> dict[str, Any]:
    """Fixed-bin descriptive analysis; no training, optimization or writes."""
    root = Path(root).resolve()
    records, invalid_final_records = _strict_finals(root)
    states = {name: _make_state() for name in
              ("legacy_240", "early_480", "forward_480")}
    for final in records:
        state = states[_cohort(final)]
        try:
            settlement = _load_saved_settlement(root, final)
        except SettlementIntegrityError:
            state["invalid_settlement"] += 1
            continue
        if settlement is None:
            state["pending"] += 1
            continue
        state["settled"] += 1
        actual = settlement.actual_side

        if final.forecast in ("ABOVE", "BELOW"):
            state["issued"][0] += 1
            state["issued"][1] += int(final.forecast == actual)
        elif final.reason != "CONFIDENCE_BELOW_69_PERCENT":
            state["other_skips"][final.reason or "UNSPECIFIED"] += 1
            continue

        if not _probability_valid(final):
            state["invalid_probability"] += 1
            continue
        band = confidence_bin(final.confidence)
        if final.forecast == "SKIP":
            correct = int(("ABOVE" if final.p_yes >= .5 else "BELOW") == actual)
            key = "skipped"
            state["skipped"][0] += 1
            state["skipped"][1] += correct
        else:
            correct = int(final.forecast == actual)
            key = "issued"
        state["bands"][band][key][0] += 1
        state["bands"][band][key][1] += correct

    groups = {key: _finalize(state) for key, state in states.items()}
    return {
        "report": "KXBTC15M_FIXED_CONFIDENCE_BAND_RELIABILITY_V1",
        "status": ("DESCRIPTIVE_ONLY" if sum(s["settled"] for s in states.values())
                   else "NO_VERIFIED_SETTLEMENTS"),
        "policy_registered_at_ns": POLICY_REGISTERED_AT_NS,
        "fixed_confidence_bins": [name for _, _, name in BANDS],
        "loaded_valid_final_records": len(records),
        "invalid_final_records": invalid_final_records,
        "cohorts": groups,
        "ready_to_promote": False,
        "threshold_changed": False,
        "model_trained": False,
        "note": (
            "Read-only analysis of original immutable decisions and locally "
            "verified official Kalshi results. Historical 240s, earlier "
            "480s and strictly post-policy-registration 480s are separate "
            "populations. Skipped-direction correctness is NOT issued "
            "forecast accuracy, nor proof of after-fee trading advantage. "
            "Fixed bins are descriptive and cannot be used to justify "
            "tuning on the same outcome labels. Wilson intervals assume "
            "independent Bernoulli trials; autocorrelation may make them "
            "optimistic. This report never promotes a model."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    args = parser.parse_args(argv)
    try:
        result = evaluate_confidence_reliability(args.root)
    except (OSError, ValueError) as exc:
        result = {"status": "EVALUATION_REFUSED", "error": str(exc),
                  "ready_to_promote": False}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
