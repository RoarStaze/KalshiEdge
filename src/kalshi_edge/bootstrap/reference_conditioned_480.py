"""Read-only, risk-conditioned BRTI/Binance reference disagreement forensics.

Join only immutable, digest-verified 480-second finals/forward features with
independently fetched official settlements. A checkpoint price is NEVER taken
from any later market outcome. No fitting, network calls or trading.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .config import BootstrapSettings
from .final_forecast import (
    COIN_FLIP_MAX_RISK_Z, FINAL_CHECKPOINT_SECONDS,
    MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
)
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts, _load_saved_settlement,
)
from .forward_feature_evidence_480 import load_forward_snapshot
from .skip_reversal_forensics_480 import (
    _CONFIDENCE_SKIP_REASONS, _forecast_probabilities, _number, _risk_bucket,
)

POLICY = "risk-conditioned-brti-binance-reference-audit-v1"
NS = 1_000_000_000
MAX_REFERENCE_AGE_NS = 5 * NS
RISK_BUCKETS = (
    "at_or_below_0_20", "above_0_20_to_0_50",
    "above_0_50_to_1_00", "above_1_00", "unavailable",
)


@dataclass(frozen=True)
class _Row:
    ticker: str
    risk_bucket: str
    disagreement: bool
    model_correct: bool
    issued: bool
    live_eligible: bool
    historical_skip: bool


def _cells(rows: Sequence[_Row]) -> dict[str, dict[str, int | float | None]]:
    result: dict[str, dict[str, int | float | None]] = {}
    for name, match in (("agree", False), ("disagree", True)):
        subset = [r for r in rows if r.disagreement is match]
        wrong = sum(not r.model_correct for r in subset)
        correct = len(subset) - wrong
        result[name] = {
            "total": len(subset), "correct": correct, "wrong": wrong,
            "error_rate": wrong / len(subset) if subset else None,
        }
    return result


def _cohort(rows: Sequence[_Row]) -> dict[str, Any]:
    return {
        "matched": len(rows),
        "overall": _cells(rows),
        "risk_strata": {
            bucket: _cells([r for r in rows if r.risk_bucket == bucket])
            for bucket in RISK_BUCKETS
        },
    }


def _policy_counts(rows: Sequence[_Row], denominator: int) -> dict[str, Any]:
    qualified = len(rows)
    correct = sum(r.model_correct for r in rows)
    return {
        "qualified": qualified,
        "correct": correct,
        "incorrect": qualified - correct,
        "skipped": denominator - qualified,
        "accuracy": correct / qualified if qualified else None,
        "coverage": qualified / denominator if denominator else None,
    }


def analyze_reference_disagreement(root: Path, *, model_hash: str) -> dict[str, Any]:
    """Condition model errors on as-of strike risk, not on future labels.

    There are three distinct denominators: all point-in-time healthy model
    directions, actual historical issued finals, and historical replay of
    the CURRENT >63% directional gate. Do not conflate actual issuance
    with a reconstructed eligibility decision.
    """
    root = Path(root).resolve()
    if (not isinstance(model_hash, str) or len(model_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in model_hash)):
        raise ValueError("audit requires one valid lowercase model SHA-256")

    excluded: Counter[str] = Counter()
    rows: list[_Row] = []
    replay_skips = replay_veto = replay_gate = 0
    seen: set[str] = set()
    for final in _forecasts(root):
        if final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
            excluded["non_480_checkpoint"] += 1
            continue
        if final.model_hash != model_hash:
            excluded["different_model"] += 1
            continue
        if final.market_ticker in seen:
            excluded["duplicate_ticker"] += 1
            continue
        seen.add(final.market_ticker)

        try:
            settlement = _load_saved_settlement(root, final)
        except (OSError, ValueError, SettlementIntegrityError):
            excluded["invalid_settlement"] += 1
            continue
        if settlement is None:
            excluded["unsettled"] += 1
            continue
        if (settlement.fetched_ts_ns <= _close_ns(final)
                or settlement.actual_side not in ("ABOVE", "BELOW")):
            excluded["invalid_settlement"] += 1
            continue
        if not _forecast_probabilities(final):
            excluded["invalid_probability"] += 1
            continue
        if final.forecast == "SKIP":
            if final.reason not in (_CONFIDENCE_SKIP_REASONS | {"COIN_FLIP_RISK_HIGH"}):
                excluded["unhealthy_skip"] += 1
                continue
            if final.reason == "COIN_FLIP_RISK_HIGH" and (
                final.coin_flip_assessment != "HIGH"
                or not _number(final.coin_flip_risk_z,
                               minimum=0, maximum=COIN_FLIP_MAX_RISK_Z)
            ):
                excluded["inconsistent_flip_veto"] += 1
                continue
        elif (final.forecast not in ("ABOVE", "BELOW")
              or final.reason is not None
              or final.forecast != ("ABOVE" if final.p_yes >= .5 else "BELOW")):
            excluded["inconsistent_issued_forecast"] += 1
            continue

        try:
            snapshot = load_forward_snapshot(root, final)
            if snapshot.status != "FEATURES_OK":
                raise ValueError("checkpoint causal features missing")
        except (OSError, ValueError):
            excluded["missing_or_invalid_snapshot"] += 1
            continue

        brti_ts = snapshot.source_max_ts_ns.get("brti")
        binance_ts = snapshot.source_max_ts_ns.get("binance")
        if (type(brti_ts) is not int or type(binance_ts) is not int
                or not 0 <= final.prediction_ts_ns - brti_ts <= MAX_REFERENCE_AGE_NS
                or not 0 <= final.prediction_ts_ns - binance_ts <= MAX_REFERENCE_AGE_NS):
            excluded["stale_feed"] += 1
            continue

        features = snapshot.features
        distance = features.get("brti_distance_to_strike_bps")
        close = features.get("btc_close")
        recorded_gap = features.get("brti_binance_gap_bps")
        if not (_number(distance, minimum=-3000, maximum=3000)
                and _number(close, minimum=.001, maximum=1e9)
                and _number(recorded_gap, minimum=-3000, maximum=3000)):
            excluded["missing_reference_features"] += 1
            continue
        binance_distance = (float(close) - final.strike) / final.strike * 10000.
        expected_gap = float(distance) - binance_distance
        if abs(float(recorded_gap) - expected_gap) > .01:
            excluded["inconsistent_reference_basis"] += 1
            continue
        if float(distance) == 0 or float(close) == final.strike:
            # Both references must establish an unambiguous strike side.
            excluded["ambiguous_reference_price"] += 1
            continue

        pside = max(float(final.p_yes), float(final.p_no))
        risk = final.coin_flip_risk_z
        veto = (final.coin_flip_assessment == "HIGH"
                or (_number(risk, minimum=0, maximum=math.inf)
                    and float(risk) <= COIN_FLIP_MAX_RISK_Z))
        eligible = False
        if pside <= MINIMUM_FINAL_DIRECTIONAL_PROBABILITY:
            replay_gate += 1
        elif veto:
            replay_veto += 1
        else:
            eligible = True
            if final.forecast == "SKIP":
                replay_skips += 1

        brti_above = float(distance) > 0
        binance_above = float(close) > final.strike
        rows.append(_Row(
            ticker=final.market_ticker,
            risk_bucket=_risk_bucket(risk),
            disagreement=brti_above != binance_above,
            model_correct=(("ABOVE" if final.p_yes >= .5 else "BELOW")
                           == settlement.actual_side),
            issued=final.forecast != "SKIP",
            live_eligible=eligible,
            historical_skip=final.forecast == "SKIP",
        ))

    actual = [r for r in rows if r.issued]
    live = [r for r in rows if r.live_eligible]
    after_veto = [r for r in live if not r.disagreement]
    rejected = [r for r in live if r.disagreement]
    return {
        "status": "DESCRIPTIVE_ONLY" if rows else "NO_VERIFIED_REFERENCE_COHORT",
        "policy": POLICY,
        "model_hash": model_hash,
        "markets_with_alignment": len(rows),
        "excluded": dict(excluded),
        "source_contract": (
            "saved 480s final + digest-checked forward feature snapshot; "
            "BRTI and Binance both <=5s old at checkpoint, signed basis "
            "arithmetically consistent; later official settlement only labels"
        ),
        "cohorts": {
            "all_healthy": _cohort(rows),
            "actual_issued": _cohort(actual),
            "live_63_replay": _cohort(live),
        },
        "policy_replay": {
            "gate": "strict predicted-side probability >0.63, historical BRTI veto",
            "eligible": len(live),
            "replayed_historical_skips": replay_skips,
            "rejected_for_near_strike_veto": replay_veto,
            "rejected_for_probability_gate": replay_gate,
        },
        "hypothetical_disagreement_veto": {
            "live_gate_before": _policy_counts(live, len(rows)),
            "live_gate_after": _policy_counts(after_veto, len(rows)),
            "correct_forecasts_removed": sum(r.model_correct for r in rejected),
            "incorrect_forecasts_avoided": sum(not r.model_correct for r in rejected),
            "trades_executed": False,
            "would_require_new_independent_validation": True,
        },
        "research_only": {
            "live_policy_changed": False,
            "model_retrained": False,
            "forecast_probabilities_modified": False,
            "network_access": False,
            "trades_executed": False,
            "outcome_as_feature": False,
            "independent_validation_claimed": False,
        },
        "limitation": (
            "DESCRIPTIVE ONLY on previously inspected settled forecasts; "
            "small/sparse risk strata and possible model-version selection "
            "prevent causal, generalizable or profitable-effect claims. "
            "A hypothetical disagreement veto can reject correct trades. "
            "Historical replay is NOT the number of actually issued forecasts. "
            "Do not modify a live model or AutoTrade gate based on this output."
        ),
    }


def _rate(value: float | None) -> str:
    return "-" if value is None else f"{value*100:.2f}%"


def _print_report(report: dict[str, Any]) -> None:
    print("KALSHIEDGE | RISK-CONDITIONED BRTI / BINANCE REFERENCE ERROR AUDIT")
    print("Status:", report["status"],
          "| Verified paired as-of references:", report["markets_with_alignment"])
    print("Model:", report["model_hash"])
    for name, label in (
        ("all_healthy", "ALL healthy model directions (includes historical skips)"),
        ("actual_issued", "ACTUALLY ISSUED historical finals"),
        ("live_63_replay", "CURRENT >63% gate replay, with BRTI veto"),
    ):
        cohort = report["cohorts"][name]
        print()
        print(label, "| markets:", cohort["matched"])
        print(f'{"BRTI risk z":25} {"AGREE wrong/n":>18} {"DISAGREE wrong/n":>20}')
        for risk, cells in (("ALL", cohort["overall"]), *cohort["risk_strata"].items()):
            a = cells["agree"]
            d = cells["disagree"]
            print(f'{risk:25} {str(a["wrong"])+"/"+str(a["total"]):>18} '
                  f'{str(d["wrong"])+"/"+str(d["total"]):>20}')
    print()
    replay = report["policy_replay"]
    print("Gate replay: eligible=", replay["eligible"],
          "| saved historical SKIPs that now qualify=", replay["replayed_historical_skips"],
          "| near-strike veto=", replay["rejected_for_near_strike_veto"],
          "| below/equal gate=", replay["rejected_for_probability_gate"], sep="")
    delta = report["hypothetical_disagreement_veto"]
    print("Hypothetical blanket disagreement veto — NOT DEPLOYED:")
    print("  Before: ", delta["live_gate_before"])
    print("  After:  ", delta["live_gate_after"])
    print("  CORRECT forecasts removed:", delta["correct_forecasts_removed"])
    print("  Incorrect forecasts avoided:", delta["incorrect_forecasts_avoided"])
    print("Excluded:", report["excluded"])
    print("NO LIVE POLICY CHANGE. NO MODEL FIT. NO TRADE.")
    print(report["limitation"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--model-hash", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    model_hash = args.model_hash
    if model_hash is None:
        from .live import load_default_bundle
        try:
            model_hash = load_default_bundle(args.root).bundle_sha256
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(f"cannot resolve active model: {exc}")
    try:
        report = analyze_reference_disagreement(args.root, model_hash=model_hash)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
