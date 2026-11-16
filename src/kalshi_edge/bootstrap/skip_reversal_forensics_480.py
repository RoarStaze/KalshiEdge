"""Read-only 480s false-SKIP and benchmark-reference error forensics.

The active promoted model, its probability, near-strike veto, AutoTrade and
configured directional threshold are NEVER changed here. Settlements label
only previously issued immutable forecasts; no after-the-fact data enters
predictor features. A retrospectively correct skipped direction was NOT an
issued forecast or trade, and cannot imply a safe/lucrative rescue.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

from .config import BootstrapSettings
from .final_forecast import COIN_FLIP_MAX_RISK_Z, FINAL_CHECKPOINT_SECONDS
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts,
    _load_saved_settlement,
)
from .forward_feature_evidence_480 import load_forward_snapshot

_POLICY = "read-only-480s-false-skips-brti-reference-v1"
_CONFIDENCE_SKIP_REASONS = frozenset({
    "DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT",
    "CONFIDENCE_BELOW_63_PERCENT",
    "CONFIDENCE_BELOW_69_PERCENT",
})


def _number(value: object, *, minimum: float, maximum: float) -> bool:
    return (type(value) in (float, int) and math.isfinite(value)
            and minimum <= value <= maximum)


def _forecast_probabilities(final: Any) -> bool:
    a, b = final.p_yes, final.p_no
    if not (_number(a, minimum=0, maximum=1)
            and _number(b, minimum=0, maximum=1)
            and abs(a + b - 1.0) <= 1e-6):
        return False
    if (final.directional_probability is not None
            and (not _number(final.directional_probability, minimum=0, maximum=1)
                 or abs(final.directional_probability - max(a, b)) > 1e-6)):
        return False
    return True


def _skip_counts() -> dict[str, Any]:
    return {
        "total": 0,
        "would_have_been_correct": 0,
        "would_have_been_wrong": 0,
        "counterfactual_accuracy": None,
    }


def _count_skip(group: dict[str, Any], correct: bool) -> None:
    group["total"] += 1
    group["would_have_been_correct" if correct else "would_have_been_wrong"] += 1


def _with_accuracy(group: dict[str, Any]) -> dict[str, Any]:
    count = group["total"]
    group["counterfactual_accuracy"] = (
        group["would_have_been_correct"] / count if count else None
    )
    return group


def _risk_bucket(z: object) -> str:
    if not _number(z, minimum=0, maximum=math.inf):
        return "unavailable"
    if z <= COIN_FLIP_MAX_RISK_Z:
        return "at_or_below_0_20"
    if z <= .5:
        return "above_0_20_to_0_50"
    if z <= 1.:
        return "above_0_50_to_1_00"
    return "above_1_00"


def analyze_skip_reversal_forensics(
    root: Path, *, model_hash: str,
) -> dict[str, Any]:
    """Score actual and missed forecast opportunities from saved evidence.

    Different model versions are NEVER pooled; unhealthy/missed checkpoint
    SKIPs cannot be retroactively rescued simply because a later outcome
    is known. BRTI/Binance cross-reference uses ONLY immutable timestamp-
    verified forward feature snapshots, never a later revised index price.
    """
    root = Path(root).resolve()
    if (not isinstance(model_hash, str) or len(model_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in model_hash)):
        raise ValueError("forensics requires a single valid promoted model SHA-256")

    excluded = Counter()
    reasons = Counter()
    accepted = _skip_counts()
    vetoed = _skip_counts()
    risk = {
        key: _skip_counts()
        for key in (
            "at_or_below_0_20", "above_0_20_to_0_50",
            "above_0_50_to_1_00", "above_1_00", "unavailable",
        )
    }
    reference = {
        "verified_checkpoints": 0,
        "snapshots_unavailable_or_invalid": 0,
        "brti_vs_binance_side_disagreements": 0,
        "disagreement_model_wrong": 0,
        "disagreement_model_correct": 0,
        "used_post_checkpoint_prices": False,
    }
    qualified = {"total": 0, "correct": 0, "incorrect": 0, "accuracy": None}
    matched = 0

    for final in _forecasts(root):
        if final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
            excluded["non_480_checkpoint"] += 1
            continue
        if final.model_hash != model_hash:
            excluded["different_model"] += 1
            continue

        try:
            outcome = _load_saved_settlement(root, final)
        except (SettlementIntegrityError, OSError, ValueError):
            excluded["invalid_settlement"] += 1
            continue
        if outcome is None:
            excluded["unsettled"] += 1
            continue
        if outcome.fetched_ts_ns <= _close_ns(final):
            excluded["invalid_settlement"] += 1
            continue

        # A stale/invalid/missing input must NEVER be reinterpreted as a
        # healthy skip merely because it happens to carry a numeric p_yes.
        if final.forecast == "SKIP" and final.reason not in (
            _CONFIDENCE_SKIP_REASONS | {"COIN_FLIP_RISK_HIGH"}
        ):
            excluded["other_unhealthy_skip"] += 1
            continue
        if final.forecast not in ("SKIP", "ABOVE", "BELOW"):
            excluded["unsupported_forecast"] += 1
            continue
        if not _forecast_probabilities(final):
            excluded["invalid_probability"] += 1
            continue

        model_side = "ABOVE" if final.p_yes >= .5 else "BELOW"
        correct = model_side == outcome.actual_side
        if final.forecast in ("ABOVE", "BELOW"):
            # The issued forecast must match the saved model's chosen side.
            # Do not silently score a contradictory or misrecorded decision.
            if final.forecast != model_side or final.reason is not None:
                excluded["contradictory_issued_forecast"] += 1
                continue
        elif final.reason == "COIN_FLIP_RISK_HIGH":
            if (not _number(final.coin_flip_risk_z, minimum=0,
                            maximum=COIN_FLIP_MAX_RISK_Z)
                    or final.coin_flip_assessment != "HIGH"):
                excluded["invalid_flip_veto"] += 1
                continue

        matched += 1
        reasons[str(final.reason or "ISSUED")] += 1

        if final.forecast in ("ABOVE", "BELOW"):
            qualified["total"] += 1
            qualified["correct" if correct else "incorrect"] += 1
        elif final.reason in _CONFIDENCE_SKIP_REASONS:
            _count_skip(accepted, correct)
        else:
            _count_skip(vetoed, correct)

        rgroup = risk[_risk_bucket(final.coin_flip_risk_z)]
        _count_skip(rgroup, correct)

        # This is additional verified, label-free *point-in-time* evidence,
        # not mandatory for scoring a model forecast or skip.
        try:
            snap = load_forward_snapshot(root, final)
            if snap.status != "FEATURES_OK":
                raise ValueError("no immutable checkpoint feature values")
            f = snap.features
            brti_bps, btc_close = (
                f.get("brti_distance_to_strike_bps"),
                f.get("btc_close"),
            )
            if (not _number(brti_bps, minimum=-3000, maximum=3000)
                    or not _number(btc_close, minimum=0.001, maximum=1e9)
                    or "brti" not in snap.source_max_ts_ns
                    or "binance" not in snap.source_max_ts_ns
                    or brti_bps == 0.0):
                raise ValueError("reference alignment evidence unavailable")
        except (OSError, ValueError):
            reference["snapshots_unavailable_or_invalid"] += 1
            continue

        reference["verified_checkpoints"] += 1
        brti_side = "ABOVE" if brti_bps > 0 else "BELOW"
        btc_side = "ABOVE" if btc_close >= final.strike else "BELOW"
        if brti_side != btc_side:
            reference["brti_vs_binance_side_disagreements"] += 1
            reference["disagreement_model_correct" if correct
                      else "disagreement_model_wrong"] += 1

    qualified["accuracy"] = (
        qualified["correct"] / qualified["total"] if qualified["total"] else None
    )
    for group in risk.values():
        _with_accuracy(group)
    _with_accuracy(accepted)
    _with_accuracy(vetoed)

    return {
        "status": "DESCRIPTIVE_ONLY" if matched else "NO_MATCHED_SETTLEMENTS",
        "policy": _POLICY,
        "model_hash": model_hash,
        "matched_settled_with_usable_probability": matched,
        "qualified": qualified,
        "probability_skips": accepted,
        "brti_flip_veto_skips": vetoed,
        "other_skip_reasons_and_exclusions": dict(reasons),
        "excluded": dict({
            key: excluded[key] for key in (
                "different_model", "non_480_checkpoint", "unsettled",
                "invalid_settlement", "other_unhealthy_skip",
                "unsupported_forecast", "invalid_probability",
                "contradictory_issued_forecast", "invalid_flip_veto",
            )
        }),
        "risk_strata": risk,
        "reference_alignment": reference,
        "actual_issued_accuracy": qualified["accuracy"],
        "actual_issued_coverage": (
            qualified["total"] / matched if matched else None
        ),
        "research_only": {
            "forecast_probability_unchanged": True,
            "trades_executed": False,
            "model_retraining": False,
            "live_gate_changed": False,
            "brti_or_binance_prices_backfilled": False,
        },
        "limitation": (
            "DESCRIPTIVE ONLY. Counterfactual correct/wrong counts for SKIPs "
            "use the originally saved p_yes direction and do not mean trades "
            "were executed, filled, or profitable. Previously inspected "
            "settlements are not an independent holdout. The reported "
            "reference alignment subset may be smaller than the full "
            "verified model cohort because historical BRTI timestamps cannot "
            "be backfilled. Do not tune/live-deploy a lower probability gate "
            "or remove the BRTI flip veto based on this analysis alone."
        ),
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value*100:.2f}%"


def _print_report(report: dict[str, Any]) -> None:
    print("KALSHIEDGE | 480s FALSE SKIPs & BRTI REVERSAL FORENSICS")
    print("Status:", report["status"],
          "| Matched settled markets:", report["matched_settled_with_usable_probability"])
    print("Original model:", report["model_hash"])
    print()
    issued = report["qualified"]
    print(f'ISSUED: {issued["total"]}, correct={issued["correct"]}, '
          f'wrong={issued["incorrect"]}, accuracy={_pct(issued["accuracy"])}')
    for label, key in (
        ("PROBABILITY SKIPS", "probability_skips"),
        ("BRTI FLIP VETO", "brti_flip_veto_skips"),
    ):
        x = report[key]
        print(f'{label}: {x["total"]}, unissued directions retrospectively '
              f'correct={x["would_have_been_correct"]}, '
              f'wrong={x["would_have_been_wrong"]}, '
              f'counterfactual accuracy={_pct(x["counterfactual_accuracy"])}')
    print("RISK STRATA (saved checkpoint z; historical model-direction correctness)")
    for label, x in report["risk_strata"].items():
        print(f' {label}: {x["total"]} samples, '
              f'{x["would_have_been_correct"]} correct, '
              f'{x["would_have_been_wrong"]} wrong')
    ref = report["reference_alignment"]
    print("BRTI / BINANCE reference comparison:")
    print(f'  verified snapshots={ref["verified_checkpoints"]}, '
          f'missing/invalid={ref["snapshots_unavailable_or_invalid"]}, '
          f'different strike sides={ref["brti_vs_binance_side_disagreements"]}, '
          f'wrong-model among disagreements={ref["disagreement_model_wrong"]}')
    print("Excluded:", report["excluded"])
    print("DESCRIPTIVE ONLY: no model training, probability change, gate change, or trades.")
    print(report["limitation"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=BootstrapSettings().bootstrap_dir,
        help="Existing local KalshiEdge bootstrap evidence directory",
    )
    parser.add_argument("--model-hash", type=str, default=None,
                        help="Single historical model SHA-256; default is active model")
    parser.add_argument("--json", action="store_true", help="Detailed machine-readable report")
    args = parser.parse_args(argv)
    model_hash = args.model_hash
    if model_hash is None:
        from .live import load_default_bundle
        try:
            model_hash = load_default_bundle(args.root).bundle_sha256
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(f"Cannot identify current promoted model: {exc}")
    try:
        report = analyze_skip_reversal_forensics(args.root, model_hash=model_hash)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
