"""Joint BRTI reference alignment and historical SKIP-rescue audit at 480s.

Read-only and unfitted. Historical settled market outcomes SCORE the originally
recorded model direction; they never determine a candidate selection. This
module does NOT run live, train, change probabilities, reduce safeguards, place
orders or write any forecast/feature/outcome data.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

from .config import BootstrapSettings
from .final_forecast import (
    COIN_FLIP_MAX_RISK_Z,
    FINAL_CHECKPOINT_SECONDS,
    MAX_CHECKPOINT_LATENESS_NS,
    MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
)
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts, _load_saved_settlement,
)
from .forward_feature_evidence_480 import load_forward_snapshot

NS = 1_000_000_000
_POLICY = "read-only-joint-brti-legacy-rescue-480-v1"
_LEGACY_STRENGTH_SKIP = frozenset({
    "CONFIDENCE_BELOW_69_PERCENT",
    "CONFIDENCE_BELOW_63_PERCENT",
})
_ALL_HEALTHY_SKIPS = _LEGACY_STRENGTH_SKIP | {
    "DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT",
    "COIN_FLIP_RISK_HIGH",
}
_BANDS = (
    ("(0.50,0.63]", 0.5, 0.63),
    ("(0.63,0.70]", 0.63, 0.70),
    ("(0.70,0.80]", 0.70, 0.80),
    ("(0.80,1.00]", 0.80, 1.00),
)


def _number(x: object, lo: float, hi: float) -> bool:
    return type(x) in (int, float) and math.isfinite(x) and lo <= x <= hi


def _empty() -> dict[str, Any]:
    return {"total": 0, "correct": 0, "wrong": 0, "accuracy": None}


def _add(bucket: dict[str, Any], correct: bool) -> None:
    bucket["total"] += 1
    bucket["correct" if correct else "wrong"] += 1


def _rate(bucket: dict[str, Any]) -> None:
    n = bucket["total"]
    bucket["accuracy"] = bucket["correct"] / n if n else None


def _reference_alignment(root: Path, record: Any, model_side: str) -> str:
    """Verified checkpoint-time reference agreement, or UNKNOWN.

    Uses the same hash-verified immutable feature evidence as the earlier
    480s research and never synthesizes a missing BRTI/spot price from
    settlement or bars observed later than the checkpoint. Exact strike
    ties are unknown (rather than silently classified ABOVE/BELOW).
    """
    try:
        snap = load_forward_snapshot(root, record)
        if snap.status != "FEATURES_OK":
            return "unavailable"
        f = snap.features
        brti_bps = f.get("brti_distance_to_strike_bps")
        btc = f.get("btc_close")
        gap_bps = f.get("brti_binance_gap_bps")
        ts = snap.prediction_ts_ns
        bt = snap.source_max_ts_ns.get("brti")
        xt = snap.source_max_ts_ns.get("binance")
        if (not _number(brti_bps, -3000, 3000)
                or not _number(btc, 0.001, 1e9)
                or not _number(gap_bps, -3000, 3000)
                or type(bt) is not int or type(xt) is not int
                or not 0 <= ts - bt <= 5 * NS
                or not 0 <= ts - xt <= 5 * NS
                or btc == record.strike or brti_bps == 0.):
            return "unavailable"
        actual_gap = brti_bps - (btc - record.strike) / record.strike * 10000.
        if abs(actual_gap - gap_bps) > 0.0001:
            return "unavailable"
    except (OSError, ValueError):
        return "unavailable"
    brti_side = "ABOVE" if brti_bps > 0 else "BELOW"
    binance_side = "ABOVE" if btc > record.strike else "BELOW"
    if brti_side != binance_side:
        return "disagree"
    return "agree_with_model" if brti_side == model_side else "agree_against_model"


def evaluate_brti_joint_rescue(root: Path, *, model_hash: str) -> dict[str, Any]:
    """Audit fixed, predeclared conjunctions on one saved-model settled cohort.

    In particular, historical 69% and 63% *strength* SKIPs are not current
    live 63% *predicted-direction-probability* SKIPs. We replay the currently
    specified 63% live probability + recorded BRTI veto as a distinct
    counterfactual policy on all available healthy, settled checkpoints.
    """
    root = Path(root).resolve()
    if (not isinstance(model_hash, str) or len(model_hash) != 64
            or any(c not in "0123456789abcdef" for c in model_hash)):
        raise ValueError("require one exact lowercase SHA-256 model hash")
    excluded: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    all_records = _empty()
    issued = _empty()
    legacy = _empty()
    joint = _empty()
    joint_confirmed = _empty()
    joint_opposed = _empty()
    joint_live = _empty()
    joint_below = _empty()
    current = {
        "qualified": 0, "correct": 0, "incorrect": 0,
        "skipped": 0, "accuracy": None, "coverage": None,
        "qualified_legacy_skips": 0,
        "correct_legacy_skips": 0, "incorrect_legacy_skips": 0,
        "skipped_probability": 0, "skipped_recorded_flip_risk": 0,
    }
    reference: Counter[str] = Counter()
    legacy_reference: Counter[str] = Counter()
    bins = [
        {
            "band": name, "historical_skips": _empty(),
            "joint_high_risk_agree": _empty(),
            "joint_and_model_agree": _empty(),
        }
        for name, _, _ in _BANDS
    ]

    for final in _forecasts(root):
        if final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
            excluded["non_480_checkpoint"] += 1
            continue
        if final.model_hash != model_hash:
            excluded["different_model"] += 1
            continue
        if (final.scheduled_ts_ns > final.prediction_ts_ns
                or final.prediction_ts_ns >
                final.scheduled_ts_ns + MAX_CHECKPOINT_LATENESS_NS):
            excluded["non_480_checkpoint"] += 1
            continue
        if final.fusion_hash is not None:
            # Avoid mixing the promoted single model with a separately fitted
            # probability fusion, even if the base model hash is unchanged.
            excluded["fused_probability_record"] += 1
            continue
        try:
            settled = _load_saved_settlement(root, final)
        except (SettlementIntegrityError, OSError, ValueError):
            excluded["invalid_settlement"] += 1
            continue
        if settled is None:
            excluded["unsettled"] += 1
            continue
        if settled.fetched_ts_ns <= _close_ns(final):
            excluded["invalid_settlement"] += 1
            continue
        if final.forecast == "SKIP" and final.reason not in _ALL_HEALTHY_SKIPS:
            excluded["other_unhealthy_skip"] += 1
            continue
        if final.forecast not in ("SKIP", "ABOVE", "BELOW"):
            excluded["unsupported_forecast"] += 1
            continue
        yes, no = final.p_yes, final.p_no
        if (not _number(yes, 0., 1.)
                or not _number(no, 0., 1.)
                or abs(yes + no - 1.) > 1e-6):
            excluded["invalid_probability"] += 1
            continue
        conf = max(yes, no)
        if (final.directional_probability is not None
                and (not _number(final.directional_probability, 0., 1.)
                     or abs(final.directional_probability - conf) > 1e-6)):
            excluded["invalid_probability"] += 1
            continue
        side = "ABOVE" if yes >= .5 else "BELOW"
        correct = side == settled.actual_side
        if final.forecast in ("ABOVE", "BELOW"):
            if final.forecast != side or final.reason is not None:
                excluded["contradictory_issued_forecast"] += 1
                continue
        elif final.reason == "COIN_FLIP_RISK_HIGH":
            z = final.coin_flip_risk_z
            if (not _number(z, 0., COIN_FLIP_MAX_RISK_Z)
                    or final.coin_flip_assessment != "HIGH"):
                excluded["invalid_flip_veto"] += 1
                continue
        _add(all_records, correct)
        reasons[str(final.reason or "ISSUED")] += 1

        is_legacy = final.forecast == "SKIP" and final.reason in _LEGACY_STRENGTH_SKIP
        if final.forecast in ("ABOVE", "BELOW"):
            _add(issued, correct)
        if is_legacy:
            _add(legacy, correct)

        z = final.coin_flip_risk_z
        veto = _number(z, 0., COIN_FLIP_MAX_RISK_Z)
        current_allowed = conf > MINIMUM_FINAL_DIRECTIONAL_PROBABILITY and not veto
        if current_allowed:
            current["qualified"] += 1
            current["correct" if correct else "incorrect"] += 1
            if is_legacy:
                current["qualified_legacy_skips"] += 1
                current["correct_legacy_skips" if correct else "incorrect_legacy_skips"] += 1
        else:
            current["skipped"] += 1
            # Live gate checks probability first, then the BRTI veto.
            current["skipped_probability" if conf <= MINIMUM_FINAL_DIRECTIONAL_PROBABILITY
                    else "skipped_recorded_flip_risk"] += 1

        reference_type = _reference_alignment(root, final, side)
        reference["agree" if reference_type.startswith("agree_")
                  else reference_type] += 1
        if not is_legacy:
            continue
        legacy_reference["agree" if reference_type.startswith("agree_")
                         else reference_type] += 1
        high_risk = _number(z, 1., math.inf) and z > 1.
        agreement = reference_type in ("agree_with_model", "agree_against_model")
        qualifies_joint = high_risk and agreement
        if qualifies_joint:
            _add(joint, correct)
            if reference_type == "agree_with_model":
                _add(joint_confirmed, correct)
            else:
                _add(joint_opposed, correct)
            if current_allowed:
                _add(joint_live, correct)
            else:
                _add(joint_below, correct)
        for group, lower, upper in zip(bins, (0.5,0.63,0.70,0.80), (0.63,0.70,0.80,1.0)):
            if lower < conf <= upper:
                _add(group["historical_skips"], correct)
                if qualifies_joint:
                    _add(group["joint_high_risk_agree"], correct)
                if qualifies_joint and reference_type == "agree_with_model":
                    _add(group["joint_and_model_agree"], correct)
                break

    for item in (all_records, issued, legacy, joint, joint_confirmed,
                 joint_opposed, joint_live, joint_below):
        _rate(item)
    for row in bins:
        _rate(row["historical_skips"])
        _rate(row["joint_high_risk_agree"])
        _rate(row["joint_and_model_agree"])

    n = all_records["total"]
    if current["qualified"] + current["skipped"] != n:
        raise ValueError("current gate replay does not account for all matched markets")
    current["accuracy"] = (
        current["correct"] / current["qualified"] if current["qualified"] else None
    )
    current["coverage"] = current["qualified"] / n if n else None

    return {
        "status": "DESCRIPTIVE_ONLY" if n else "NO_VERIFIED_SETTLED_MARKETS",
        "policy": _POLICY,
        "model_hash": model_hash,
        "matched_settled_markets": n,
        "all_saved_model_directions": all_records,
        "historical_issued": issued,
        "historical_strength_skips": legacy,
        "historical_strength_skip_reasons": {
            r: reasons[r] for r in sorted(_LEGACY_STRENGTH_SKIP) if reasons[r]
        },
        "joint_risk_gt_1_and_reference_agreement": joint,
        "joint_plus_model_side_agreement": joint_confirmed,
        "joint_reference_against_model": joint_opposed,
        "joint_above_current_gate": joint_live,
        "joint_at_or_below_current_gate": joint_below,
        "reference_status": {
            key: reference[key] for key in ("agree", "disagree", "unavailable")
        },
        "legacy_skip_reference_status": {
            key: legacy_reference[key] for key in ("agree", "disagree", "unavailable")
        },
        "historical_skip_probability_bands": bins,
        "current_63_gate_counterfactual": current,
        "excluded": {
            name: excluded[name] for name in (
                "different_model", "non_480_checkpoint",
                "fused_probability_record", "unsettled", "invalid_settlement",
                "other_unhealthy_skip", "unsupported_forecast",
                "invalid_probability", "contradictory_issued_forecast",
                "invalid_flip_veto",
            )
        },
        "research_only": {
            "model_retraining": False, "live_gate_changed": False,
            "trades_executed": False, "retrospective_rescue_only": True,
            "forecast_probability_unchanged": True,
            "post_checkpoint_prices_used": False,
        },
        "limitation": (
            "DESCRIPTIVE ONLY; previously inspected historical 480-second "
            "markets are NOT independent validation. The original 63% and 69% "
            "SKIPs used the STRENGTH gate; current 63% checks PREDICTED-SIDE "
            "PROBABILITY. This is a replay on eligible saved model outputs, "
            "not new issued decisions or real trades. In particular, rescued "
            "markets at/below the current 63% live gate must NOT be deployed. "
            "Missing, stale, tied or hash-invalid historical reference data "
            "stays unavailable rather than filled with hindsight. Signal "
            "confirmation is not calibrated evidence of profitability."
        ),
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{100*value:.2f}%"


def _print_report(r: dict[str, Any]) -> None:
    print("KALSHIEDGE | JOINT BRTI RESCUE & 480s HISTORICAL SKIPs")
    print("Status:", r["status"], "| Paired settled markets:", r["matched_settled_markets"])
    print("Frozen base model SHA-256:", r["model_hash"])
    for title, key in (
        ("HISTORICAL ISSUED", "historical_issued"),
        ("HISTORICAL STRENGTH SKIPs", "historical_strength_skips"),
        ("JOINT: z>1 AND BRTI/Binance agree", "joint_risk_gt_1_and_reference_agreement"),
        ("JOINT PLUS MODEL-SIDE AGREEMENT", "joint_plus_model_side_agreement"),
        ("JOINT ABOVE LIVE 63% GATE", "joint_above_current_gate"),
        ("JOINT BELOW OR EQUAL TO LIVE GATE", "joint_at_or_below_current_gate"),
    ):
        item = r[key]
        print(f'{title}: {item["total"]}, correct={item["correct"]}, '
              f'wrong={item["wrong"]}, accuracy={_pct(item["accuracy"])}')
    gate = r["current_63_gate_counterfactual"]
    print(
        f'LIVE 63% REPLAY: qualified={gate["qualified"]}, skipped={gate["skipped"]}, '
        f'correct={gate["correct"]}, wrong={gate["incorrect"]}, '
        f'accuracy={_pct(gate["accuracy"])}, coverage={_pct(gate["coverage"])}'
    )
    print("Newly eligible LEGACY strength SKIPs in live replay:",
          gate["qualified_legacy_skips"],
          "(correct:", gate["correct_legacy_skips"],
          "wrong:", gate["incorrect_legacy_skips"], ")")
    print("Reference coverage:", r["reference_status"],
          "| Historical strength SKIPs:", r["legacy_skip_reference_status"])
    print("HISTORICAL SKIP PROBABILITY BANDS (fixed, descriptive):")
    for b in r["historical_skip_probability_bands"]:
        all_=b["historical_skips"]
        joint=b["joint_high_risk_agree"]
        confirmed=b["joint_and_model_agree"]
        print(f' {b["band"]:>12}: skipped={all_["total"]} '
              f'joint={joint["total"]} ({joint["correct"]} correct, '
              f'{joint["wrong"]} wrong), '
              f'triple-confirmed={confirmed["total"]}')
    print("Excluded:", r["excluded"])
    print("DESCRIPTIVE ONLY: no retraining, trades, model/gate changes, or lookahead.")
    print(r["limitation"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--model-hash", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sha=args.model_hash
    if sha is None:
        from .live import load_default_bundle
        try:
            sha=load_default_bundle(args.root).bundle_sha256
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(f"Could not resolve active promoted model: {exc}")
    try:
        result=evaluate_brti_joint_rescue(args.root, model_hash=sha)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    else:
        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
