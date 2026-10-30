from __future__ import annotations

"""Read-only BRTI/Binance comparison at immutable final checkpoints."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def compare_brti_alignment(
    forecasts: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_key: dict[tuple[str, int], list[Mapping[str, Any]]] = {}

    for pred in predictions:
        ticker = pred.get("market_ticker")
        ts = pred.get("prediction_ts_ns")
        if (
            isinstance(ticker, str)
            and isinstance(ts, int)
            and not isinstance(ts, bool)
        ):
            by_key.setdefault((ticker, ts), []).append(pred)

    examples: list[dict[str, Any]] = []
    missing = duplicate = rejected = 0

    for forecast in forecasts:
        if forecast.get("actual_side") not in ("ABOVE", "BELOW"):
            continue

        if (
            forecast.get("forecast") == "SKIP"
            and forecast.get("reason") != "CONFIDENCE_BELOW_69_PERCENT"
        ):
            rejected += 1
            continue

        ticker = forecast.get("market_ticker")
        ts = forecast.get("prediction_ts_ns")

        if not isinstance(ticker, str) or not isinstance(ts, int):
            rejected += 1
            continue

        matches = by_key.get((ticker, ts), [])

        if not matches:
            missing += 1
            continue

        if len(matches) != 1:
            duplicate += 1
            continue

        pred = matches[0]
        quality = pred.get("feed_quality")

        healthy = (
            isinstance(quality, dict)
            and quality.get("healthy") is True
        )

        if pred.get("status") != "OK" or not healthy:
            rejected += 1
            continue

        if forecast.get("model_hash") != pred.get("model_hash"):
            rejected += 1
            continue

        strike = _number(forecast.get("strike"))
        live_strike = _number(pred.get("strike"))
        brti = _number(pred.get("brti_value"))
        btc = _number(pred.get("btc_reference"))
        p_yes = _number(pred.get("p_yes"))
        p_no = _number(pred.get("p_no"))
        final_yes = _number(forecast.get("p_yes"))
        final_no = _number(forecast.get("p_no"))

        if (
            strike is None or strike <= 0
            or live_strike is None
            or abs(strike - live_strike) > .001
            or brti is None or brti <= 0
            or btc is None or btc <= 0
            or p_yes is None or p_no is None
            or not (0 <= p_yes <= 1 and 0 <= p_no <= 1)
            or abs(p_yes + p_no - 1) > 1e-6
            or final_yes is None or final_no is None
            or abs(p_yes - final_yes) > 1e-8
            or abs(p_no - final_no) > 1e-8
        ):
            rejected += 1
            continue

        actual = forecast["actual_side"]

        brti_side = "ABOVE" if brti >= strike else "BELOW"
        btc_side = "ABOVE" if btc >= strike else "BELOW"
        model_side = "ABOVE" if p_yes >= p_no else "BELOW"

        examples.append({
            "market_ticker": ticker,
            "brti_minus_binance_dollars": round(brti - btc, 4),
            "absolute_brti_binance_gap_bps":
                abs(brti - btc) / strike * 10000,
            "brti_minus_strike_dollars": round(brti - strike, 4),
            "model_side": model_side,
            "brti_checkpoint_side": brti_side,
            "binance_checkpoint_side": btc_side,
            "actual_settlement_side": actual,
            "model_correct": model_side == actual,
            "brti_checkpoint_correct": brti_side == actual,
            "binance_checkpoint_correct": btc_side == actual,
        })

    total = len(examples)

    settled_count = sum(
        f.get("actual_side") in ("ABOVE", "BELOW")
        for f in forecasts
    )

    examples.sort(
        key=lambda row: row["absolute_brti_binance_gap_bps"],
        reverse=True,
    )

    return {
        "report": "V6_STAGE2_READ_ONLY_BRTI_ALIGNMENT",
        "settled_forecasts": settled_count,
        "matched_healthy_checkpoints": total,
        "missing_exact_checkpoint_records": missing,
        "duplicate_exact_checkpoint_records": duplicate,
        "excluded_unhealthy_or_inconsistent": rejected,
        "mean_absolute_brti_binance_gap_bps": (
            mean(
                x["absolute_brti_binance_gap_bps"]
                for x in examples
            )
            if total else None
        ),
        "max_absolute_brti_binance_gap_bps": (
            max(
                x["absolute_brti_binance_gap_bps"]
                for x in examples
            )
            if total else None
        ),
        "reference_direction_disagreements": sum(
            x["brti_checkpoint_side"] != x["binance_checkpoint_side"]
            for x in examples
        ),
        "model_direction_accuracy": (
            sum(x["model_correct"] for x in examples) / total
            if total else None
        ),
        "brti_checkpoint_direction_accuracy": (
            sum(x["brti_checkpoint_correct"] for x in examples) / total
            if total else None
        ),
        "binance_checkpoint_direction_accuracy": (
            sum(x["binance_checkpoint_correct"] for x in examples) / total
            if total else None
        ),
        "largest_reference_gaps": examples[:5],
        "warning": (
            "Descriptive exact-checkpoint audit only. "
            "Not a trained challenger, independent validation, "
            "or model promotion decision."
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

    for item in _forecasts(root):
        try:
            outcome = _load_saved_settlement(root, item)
        except SettlementIntegrityError:
            continue

        if outcome is None:
            continue

        record = item.model_dump(mode="json")
        record["actual_side"] = outcome.actual_side
        settled.append(record)

    requested = {
        (f["market_ticker"], f["prediction_ts_ns"])
        for f in settled
    }

    days = {
        datetime.fromtimestamp(
            f["prediction_ts_ns"] // 1_000_000_000,
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
                    row = json.loads(line)

                    if (
                        isinstance(row, dict)
                        and (
                            row.get("market_ticker"),
                            row.get("prediction_ts_ns"),
                        ) in requested
                    ):
                        predictions.append(row)

                except (ValueError, TypeError):
                    continue

    report = compare_brti_alignment(settled, predictions)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
