from __future__ import annotations

"""Read-only paired evaluation of live V5 and BRTI shadow probabilities."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

NS = 1_000_000_000
ELIGIBLE_SKIP_REASON = "CONFIDENCE_BELOW_69_PERCENT"


def _probability(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _metrics(pairs: Sequence[tuple[int, float]]) -> dict:
    if not pairs:
        return {
            "markets": 0, "correct": 0,
            "accuracy": None, "brier": None, "log_loss": None,
        }

    n = len(pairs)
    correct = sum((p >= .5) == bool(y) for y, p in pairs)

    brier = sum((p - y) ** 2 for y, p in pairs) / n

    loss = -sum(
        y * math.log(max(1e-15, min(1 - 1e-15, p))) +
        (1 - y) * math.log(max(1e-15, min(1 - 1e-15, 1 - p)))
        for y, p in pairs
    ) / n

    return {
        "markets": n,
        "correct": correct,
        "accuracy": correct / n,
        "brier": brier,
        "log_loss": loss,
    }


def compare_shadow(
    forecasts: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

    by_key: dict[tuple[str, int], list[Mapping[str, Any]]] = {}

    for pred in predictions:
        ticker = pred.get("market_ticker")
        ts = pred.get("prediction_ts_ns")

        if isinstance(ticker, str) and type(ts) is int:
            by_key.setdefault((ticker, ts), []).append(pred)

    counts = {
        "settled_forecasts": 0,
        "excluded_nonmodel_skips": 0,
        "missing_exact_predictions": 0,
        "duplicate_exact_predictions": 0,
        "without_stage3_shadow_fields": 0,
        "rejected_unhealthy_or_inconsistent": 0,
        "paired_markets": 0,
        "v5_only_correct": 0,
        "v6_only_correct": 0,
        "both_correct": 0,
        "both_wrong": 0,
    }

    baseline = []
    shadow = []
    gaps = []
    samples = []
    seen = set()

    for forecast in forecasts:
        actual = forecast.get("actual_side")

        if actual not in ("ABOVE", "BELOW"):
            continue

        counts["settled_forecasts"] += 1
        decision = forecast.get("forecast")

        eligible = (
            decision in ("ABOVE", "BELOW")
            or (
                decision == "SKIP"
                and forecast.get("reason") == ELIGIBLE_SKIP_REASON
            )
        )

        if not eligible:
            counts["excluded_nonmodel_skips"] += 1
            continue

        ticker = forecast.get("market_ticker")
        ts = forecast.get("prediction_ts_ns")
        scheduled = forecast.get("scheduled_ts_ns")

        if (
            not isinstance(ticker, str)
            or type(ts) is not int
            or type(scheduled) is not int
            or not scheduled <= ts <= scheduled + 2 * NS
            or ticker in seen
        ):
            counts["rejected_unhealthy_or_inconsistent"] += 1
            continue

        seen.add(ticker)
        matches = by_key.get((ticker, ts), [])

        if not matches:
            counts["missing_exact_predictions"] += 1
            continue

        if len(matches) != 1:
            counts["duplicate_exact_predictions"] += 1
            continue

        pred = matches[0]

        if pred.get("brti_shadow_probability") is None:
            counts["without_stage3_shadow_fields"] += 1
            continue

        quality = pred.get("feed_quality")

        if (
            pred.get("status") != "OK"
            or not isinstance(quality, dict)
            or quality.get("healthy") is not True
        ):
            counts["rejected_unhealthy_or_inconsistent"] += 1
            continue

        fyes = _probability(forecast.get("p_yes"))
        fno = _probability(forecast.get("p_no"))
        v5 = _probability(pred.get("p_yes"))
        v5no = _probability(pred.get("p_no"))
        v6 = _probability(pred.get("brti_shadow_probability"))

        bt = pred.get("brti_source_ts_ns")
        bn = pred.get("binance_source_ts_ns")

        try:
            remaining = float(pred.get("seconds_remaining"))
            gap = float(pred.get("brti_binance_gap_bps"))
        except (ValueError, TypeError, OverflowError):
            remaining = math.nan
            gap = math.nan

        valid = (
            fyes is not None and fno is not None
            and v5 is not None and v5no is not None
            and v6 is not None
            and abs(fyes + fno - 1) <= 1e-6
            and abs(v5 + v5no - 1) <= 1e-6
            and abs(fyes - v5) <= 1e-8
            and abs(fno - v5no) <= 1e-8
            and isinstance(forecast.get("model_hash"), str)
            and bool(forecast.get("model_hash"))
            and pred.get("model_hash") == forecast.get("model_hash")
            and pred.get("model_version") == forecast.get("model_version")
            and type(bt) is int and type(bn) is int
            and 0 <= ts - bt <= 5 * NS
            and 0 <= ts - bn <= 5 * NS
            and math.isfinite(remaining)
            and 238 <= remaining <= 240.01
            and math.isfinite(gap)
        )

        if not valid:
            counts["rejected_unhealthy_or_inconsistent"] += 1
            continue

        label = int(actual == "ABOVE")

        baseline.append((label, v5))
        shadow.append((label, v6))
        gaps.append(abs(gap))

        correct_v5 = (v5 >= .5) == bool(label)
        correct_v6 = (v6 >= .5) == bool(label)

        if correct_v5 and correct_v6:
            counts["both_correct"] += 1
        elif not correct_v5 and not correct_v6:
            counts["both_wrong"] += 1
        elif correct_v5:
            counts["v5_only_correct"] += 1
        else:
            counts["v6_only_correct"] += 1

        samples.append({
            "market_ticker": ticker,
            "actual": actual,
            "v5_yes": v5,
            "v6_yes": v6,
            "v5_correct": correct_v5,
            "v6_correct": correct_v6,
            "brti_binance_gap_bps": gap,
        })

    counts["paired_markets"] = len(baseline)

    v5_metrics = _metrics(baseline)
    v6_metrics = _metrics(shadow)
    n = len(baseline)

    return {
        "report": "V6_STAGE4_PAIRED_SHADOW_EVALUATION",
        **counts,
        "v5": v5_metrics,
        "v6_shadow": v6_metrics,
        "delta_v6_minus_v5_accuracy": (
            v6_metrics["accuracy"] - v5_metrics["accuracy"]
            if n else None
        ),
        "delta_v6_minus_v5_brier": (
            v6_metrics["brier"] - v5_metrics["brier"]
            if n else None
        ),
        "delta_v6_minus_v5_log_loss": (
            v6_metrics["log_loss"] - v5_metrics["log_loss"]
            if n else None
        ),
        "mean_abs_brti_binance_gap_bps": (
            sum(gaps) / n if n else None
        ),
        "status": (
            "AWAITING_NEW_SHADOW_SETTLEMENTS"
            if not n else "RESEARCH_ONLY_NOT_PROMOTION_EVIDENCE"
        ),
        "sample_markets": samples[:10],
        "warning": (
            "Prospective shadow comparison only. No model promotion. "
            "Results are descriptive and are not independent "
            "confirmation of predictive improvement."
        ),
    }


def main() -> int:
    from .config import BootstrapSettings
    from .final_history import (
        _forecasts,
        _load_saved_settlement,
        SettlementIntegrityError,
    )

    root = BootstrapSettings().bootstrap_dir.resolve()
    settled = []

    for record in _forecasts(root):
        try:
            official = _load_saved_settlement(root, record)
        except SettlementIntegrityError:
            continue

        if official is None:
            continue

        item = record.model_dump(mode="json")
        item["actual_side"] = official.actual_side
        settled.append(item)

    requested = {
        (f["market_ticker"], f["prediction_ts_ns"])
        for f in settled
    }

    days = {
        datetime.fromtimestamp(
            f["prediction_ts_ns"] / NS,
            tz=timezone.utc,
        ).date().isoformat()
        for f in settled
    }

    predictions = []

    for day in sorted(days):
        path = root / "predictions" / day / "predictions.jsonl"

        if not path.is_file():
            continue

        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue

                if (
                    isinstance(item, dict)
                    and (
                        item.get("market_ticker"),
                        item.get("prediction_ts_ns"),
                    ) in requested
                ):
                    predictions.append(item)

    report = compare_shadow(settled, predictions)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
