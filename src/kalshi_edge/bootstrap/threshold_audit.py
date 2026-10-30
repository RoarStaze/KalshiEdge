from __future__ import annotations

"""Read-only research on final-forecast probability thresholds."""

import json
import math
from typing import Any, Mapping, Sequence

THRESHOLDS = (0.69, 0.75, 0.80, 0.845, 0.90, 0.95)


def compare_thresholds(
    forecasts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    settled = [
        row for row in forecasts
        if row.get("settlement_status") == "SETTLED"
    ]
    eligible: list[tuple[float, float, str, str]] = []

    for row in settled:
        decision = row.get("forecast")

        # Do not reinterpret missing-feed or late-checkpoint skips.
        if decision not in ("ABOVE", "BELOW") and not (
            decision == "SKIP"
            and row.get("reason") == "CONFIDENCE_BELOW_69_PERCENT"
        ):
            continue

        actual = row.get("actual_side")
        if actual not in ("ABOVE", "BELOW"):
            continue

        try:
            yes = float(row["p_yes"])
            no = float(row["p_no"])
        except (TypeError, ValueError, KeyError, OverflowError):
            continue

        if not (math.isfinite(yes) and math.isfinite(no)):
            continue
        if not (
            0 <= yes <= 1
            and 0 <= no <= 1
            and abs(yes + no - 1) <= 1e-6
        ):
            continue

        version = str(row.get("model_version") or "UNKNOWN")
        eligible.append((yes, no, actual, version))

    results = []

    for threshold in THRESHOLDS:
        selected = [
            ("ABOVE" if yes >= no else "BELOW", actual)
            for yes, no, actual, _version in eligible
            if max(yes, no) >= threshold
        ]

        correct = sum(
            predicted == actual
            for predicted, actual in selected
        )
        count = len(selected)

        results.append({
            "minimum_probability": threshold,
            "predictions": count,
            "correct": correct,
            "incorrect": count - correct,
            "accuracy": correct / count if count else None,
            "coverage": (
                count / len(eligible) if eligible else None
            ),
        })

    brier = (
        sum(
            (yes - int(actual == "ABOVE")) ** 2
            for yes, _no, actual, _version in eligible
        ) / len(eligible)
        if eligible else None
    )

    return {
        "report": "V6_STAGE1_READ_ONLY_THRESHOLD_AUDIT",
        "settled_markets": len(settled),
        "eligible_markets": len(eligible),
        "excluded_markets": len(settled) - len(eligible),
        "model_versions": sorted({
            version for *_other, version in eligible
        }),
        "brier_all_eligible": brier,
        "thresholds": results,
        "warning": (
            "Retrospective descriptive results only. "
            "Not an independent validation or promotion decision. "
            "Do not reuse a consumed lockbox to select a threshold."
        ),
    }


def main() -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    root = BootstrapSettings().bootstrap_dir
    history = read_final_history(root)
    report = compare_thresholds(history["forecasts"])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
