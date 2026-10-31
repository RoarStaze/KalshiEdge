from __future__ import annotations

"""Read-only, leakage-aware summary of settled live BTC forecasts.

This does NOT fit a calibrator, optimize thresholds, or promote models.
Only already settled, ticker-unique, timestamp-valid rows are scored.
The threshold cohorts are descriptive retrospective cohorts, not fresh
independent validation. Always compare future samples to a market baseline.
"""

import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .metrics import probability_metrics

CONFIDENCE_COHORTS = (0.0, 0.69, 0.80, 0.90)


def _verified(row: Mapping[str, Any]):
    """Return (close_ts, ticker, p_yes, actual, model) or None."""
    if row.get("settlement_status") != "SETTLED":
        return None
    forecast = row.get("forecast")
    if forecast not in ("ABOVE", "BELOW") and not (
        forecast == "SKIP" and row.get("reason") == "CONFIDENCE_BELOW_69_PERCENT"
    ):
        return None
    ticker = row.get("market_ticker")
    actual = row.get("actual_side")
    if not isinstance(ticker, str) or not ticker or actual not in ("ABOVE", "BELOW"):
        return None
    yes, no = row.get("p_yes"), row.get("p_no")
    if type(yes) not in (int, float) or type(no) not in (int, float):
        return None
    if not (math.isfinite(yes) and math.isfinite(no)
            and 0 <= yes <= 1 and 0 <= no <= 1 and abs(yes + no - 1) <= 1e-6):
        return None
    try:
        dt = datetime.fromisoformat(str(row.get("market_close")).replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        ts = dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    model = row.get("model_version")
    if not isinstance(model, str) or not model:
        model = "UNKNOWN"
    return ts, ticker, float(yes), actual, model


def _metrics(rows):
    if not rows:
        return None
    m = probability_metrics(
        [int(row[3] == "ABOVE") for row in rows], [row[2] for row in rows]
    )
    return {
        "markets": len(rows), "brier": m.brier, "log_loss": m.log_loss,
        "accuracy": m.accuracy, "ece": m.ece, "sharpness": m.sharpness,
        "reliability_bins": [bin.model_dump(mode="json") for bin in m.reliability_bins],
    }


def _market_quote(row: Mapping[str, Any]) -> tuple[float, float] | None:
    """Extract authentic same-checkpoint YES bid/ask, never stale fallback prior."""
    bid, ask = row.get("kalshi_yes_bid"), row.get("kalshi_yes_ask")
    if type(bid) not in (int, float) or type(ask) not in (int, float):
        return None
    if not (math.isfinite(bid) and math.isfinite(ask) and 0 <= bid <= ask <= 1):
        return None
    return ((bid + ask) / 2, ask - bid)


def _market_comparison(matched, total: int) -> dict[str, Any]:
    """Proper scores on identical settled markets; midpoint is not an ask."""
    metadata = {
        "baseline_type": "DECISION_TIME_KALSHI_YES_BID_ASK_MIDPOINT",
        "matched_markets": len(matched),
        "total_scored_markets": total,
        "markets_without_valid_reference_quote": total - len(matched),
        "independent_validation": False,
        "warning": "Bid/ask midpoint is not executable price, true probability, or verified trading edge.",
    }
    if not matched:
        return {
            **metadata,
            "ai_brier": None, "market_midpoint_brier": None, "brier_improvement": None,
            "ai_log_loss": None, "market_midpoint_log_loss": None,
            "log_loss_improvement": None, "mean_market_spread": None,
        }

    labels = [int(r[3] == "ABOVE") for r, _p, _spread in matched]
    ai = probability_metrics(labels, [r[2] for r, _p, _spread in matched])
    market = probability_metrics(labels, [p for _r, p, _spread in matched])
    return {
        **metadata,
        "ai_brier": ai.brier,
        "market_midpoint_brier": market.brier,
        "brier_improvement": market.brier - ai.brier,
        "ai_log_loss": ai.log_loss,
        "market_midpoint_log_loss": market.log_loss,
        "log_loss_improvement": market.log_loss - ai.log_loss,
        "mean_market_spread": sum(spread for _r, _p, spread in matched) / len(matched),
    }


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float | None:
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    return max(0.0, (p + z2 / (2 * total) -
                    z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)))
               / (1 + z2 / total))


def evaluate_forward_reliability(
    forecasts: Sequence[Mapping[str, Any]],
    *, min_sample: int = 100, windows: tuple[int, ...] = (50, 200),
) -> dict[str, Any]:
    """Assess immutable settled outcomes without mutating model or trade state.

    Duplicate *settled eligible* ticker rows are excluded entirely rather
    than arbitrarily picking a winning forecast. Malformed rows don't count.
    No in-sample threshold tuning or interpretation as proof of edge.
    """
    if not isinstance(min_sample, int) or isinstance(min_sample, bool) or min_sample < 1:
        raise ValueError("min_sample must be a positive integer")
    if any(type(window) is not int or window <= 0 for window in windows):
        raise ValueError("windows must contain positive integers")

    pending = invalid = 0
    eligible = []
    checkpoint_quotes = {}
    for row in forecasts:
        if not isinstance(row, Mapping):
            invalid += 1
            continue
        if row.get("settlement_status") != "SETTLED":
            pending += 1
            continue
        checked = _verified(row)
        if checked is None:
            invalid += 1
        else:
            eligible.append(checked)
            quote = _market_quote(row)
            if quote is not None:
                checkpoint_quotes[checked[1]] = quote

    counts = Counter(item[1] for item in eligible)
    duplicates = sum(count for count in counts.values() if count > 1)
    scored = sorted(
        (row for row in eligible if counts[row[1]] == 1),
        key=lambda row: (row[0], row[1]),
    )
    overall = _metrics(scored)
    matched = [(r, *checkpoint_quotes[r[1]]) for r in scored
               if r[1] in checkpoint_quotes]
    comparison = _market_comparison(matched, len(scored))
    by_model = {}
    by_model_reference = {}
    for name in sorted({r[4] for r in scored}):
        model_rows = [r for r in scored if r[4] == name]
        by_model[name] = _metrics(model_rows)
        by_model_reference[name] = _market_comparison(
            [entry for entry in matched if entry[0][4] == name],
            len(model_rows),
        )

    groups = []
    for floor in CONFIDENCE_COHORTS:
        selected = [
            r for r in scored
            if 2.0 * abs(r[2] - 0.5) + 1e-12 >= floor
        ]
        total = len(selected)
        wins = sum((r[2] >= 0.5) == (r[3] == "ABOVE") for r in selected)
        avg_win = sum(max(r[2], 1.0 - r[2]) for r in selected) / total if total else None
        groups.append({
            "minimum_confidence": floor,
            "minimum_model_win_probability": round((1 + floor) / 2, 6),
            "markets": total, "wins": wins,
            "observed_accuracy": wins / total if total else None,
            "mean_model_win_probability": avg_win,
            "wilson_lower_95": _wilson_lower(wins, total),
            "calibration_gap": avg_win - wins / total if total else None,
        })

    windows_out = []
    for n in windows:
        last = scored[-n:]
        metrics = _metrics(last)
        windows_out.append({
            "window_markets": n, "markets": len(last),
            "first_market": last[0][1] if last else None,
            "last_market": last[-1][1] if last else None,
            "accuracy": metrics["accuracy"] if metrics else None,
            "brier": metrics["brier"] if metrics else None,
            "log_loss": metrics["log_loss"] if metrics else None,
            "ece": metrics["ece"] if metrics else None,
        })
    return {
        "report": "KXBTC15M_READ_ONLY_SETTLED_FORECAST_RELIABILITY_V1",
        "markets_scored": len(scored),
        "pending_excluded": pending,
        "ineligible_excluded": invalid,
        "duplicates_excluded": duplicates,
        "minimum_required_markets": min_sample,
        "has_sufficient_evidence": len(scored) >= min_sample,
        "readiness": "DESCRIPTIVE_ONLY" if len(scored) >= min_sample
        else "INSUFFICIENT_SAMPLE",
        "metrics": overall, "by_model": by_model,
        "market_midpoint_comparison": comparison,
        "market_midpoint_by_model": by_model_reference,
        "confidence_groups": groups, "windows": windows_out,
        "independent_validation": False,
        "warning": (
            "Retrospective, descriptive ONLY. Confidence is distance from 0.5, "
            "not an empirically calibrated winning frequency. Even sufficient "
            "sample size is not proof of independent out-of-sample skill or "
            "net trading profitability. Never promote a model or tune live "
            "thresholds using these already-seen settlements."
        ),
    }


def main() -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    root = BootstrapSettings().bootstrap_dir
    forecasts = read_final_history(root)["forecasts"]
    print(json.dumps(evaluate_forward_reliability(forecasts), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
