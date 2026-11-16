"""Frozen, prospective 480s KalshiEdge reliability and error monitoring.

Exactly ONE start-time and incumbent model/gate/veto registration is stored
before future forecasts are issued. Scores use separately saved official
settlements; BRTI and Binance comparisons use only hash-bound causal
features saved with the ORIGINAL forecast. No fitting, threshold search,
trade, exchange credential, API call, or live prediction modification.

A 480-recorded-market prospective horizon is fixed at registration. Running
--status repeatedly updates the descriptive progress *without* silently
changing the horizon, selected markets, or selection rules. A completed
horizon is evidence, not automatic authorization to change AutoTrade.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any

from .brti_joint_rescue_audit_480 import _reference_alignment
from .config import BootstrapSettings
from .final_forecast import (
    COIN_FLIP_MAX_RISK_Z, COIN_FLIP_POLICY,
    FINAL_CHECKPOINT_SECONDS, MAX_CHECKPOINT_LATENESS_NS,
    MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
)
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts, _load_saved_settlement,
)
from .forward_feature_evidence_480 import load_forward_snapshot
from .metrics import probability_metrics

NS = 1_000_000_000
SCHEMA = "KALSHIEDGE_FROZEN_PROSPECTIVE_480_V1"
MANIFEST_FILE = Path("research/prospective_monitor_480/frozen_baseline.json")
HORIZON_RECORDED_MARKETS = 480
PRIMARY_COMPARISON = "REFERENCE_DISAGREEMENT_VS_AGREEMENT"
_SHA_CHARS = frozenset("0123456789abcdef")
_PROBABILITY_SKIP = "DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT"
_FLIP_SKIP = "COIN_FLIP_RISK_HIGH"
_REFERENCE_CLASSES = ("agree_with_model", "agree_against_model",
                      "disagree", "unavailable")


def _canon(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _valid_model_hash(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(ch in _SHA_CHARS for ch in value)
    )


def freeze_prospective_monitor(
    root: Path, *, model_hash: str, now_ns: int,
    target_recorded_markets: int = HORIZON_RECORDED_MARKETS,
) -> dict[str, Any]:
    """Register once with the *current* clock; refuse overwrite/re-anchor.

    The timestamp is injectable for deterministic tests. Production CLI
    provides no timestamp override to avoid a backdated fake holdout.
    """
    if (not _valid_model_hash(model_hash) or type(now_ns) is not int
            or now_ns <= 0 or type(target_recorded_markets) is not int
            or not 1 <= target_recorded_markets <= 100000):
        raise ValueError("Invalid frozen model, timestamp, or horizon")
    root = Path(root).resolve()
    if root.name == "raw" and root.parent.name == "data":
        raise ValueError("Refusing to register inside canonical raw data")
    body = {
        "schema": SCHEMA,
        "model_hash": model_hash,
        "started_at_ns": now_ns,
        "minimum_directional_probability": MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
        "brti_flip_veto_z": COIN_FLIP_MAX_RISK_Z,
        "brti_flip_policy": COIN_FLIP_POLICY,
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
        "fixed_horizon_recorded_markets": target_recorded_markets,
        "primary_comparison": PRIMARY_COMPARISON,
        "candidate_error_diagnostic": (
            "MODEL_ERROR_CONDITIONED_ON_CAUSAL_BRTI_BINANCE_REFERENCE_DISAGREEMENT"
        ),
        "below_gate_observation": (
            "SAVED_MODEL_SIDE_AND_Z_GT_1_AND_BOTH_REFERENCES_AGREE_WITH_MODEL"
        ),
        "model_fusion_hash": None,
    }
    encoded = {**body, "sha256": hashlib.sha256(_canon(body)).hexdigest()}
    path = root / MANIFEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_canon(encoded).decode("utf-8") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        # Never replace the boundary with one chosen after seeing outcomes.
        raise
    return load_frozen_manifest(root)


def load_frozen_manifest(root: Path) -> dict[str, Any]:
    path = Path(root).resolve() / MANIFEST_FILE
    try:
        raw = path.read_bytes()
        if len(raw) > 4096:
            raise ValueError("frozen manifest integrity: oversized")
        doc = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("frozen manifest integrity: missing or unreadable") from exc
    if not isinstance(doc, dict):
        raise ValueError("frozen manifest integrity: wrong format")
    digest = doc.get("sha256")
    body = {k: v for k, v in doc.items() if k != "sha256"}
    if (not isinstance(digest, str)
            or hashlib.sha256(_canon(body)).hexdigest() != digest):
        raise ValueError("frozen manifest integrity: SHA-256 mismatch")
    if (body.get("schema") != SCHEMA
            or not _valid_model_hash(body.get("model_hash"))
            or type(body.get("started_at_ns")) is not int
            or body["started_at_ns"] <= 0
            or body.get("checkpoint_seconds") != FINAL_CHECKPOINT_SECONDS
            or body.get("primary_comparison") != PRIMARY_COMPARISON
            or body.get("model_fusion_hash") is not None
            or type(body.get("fixed_horizon_recorded_markets")) is not int
            or not 1 <= body["fixed_horizon_recorded_markets"] <= 100000
            or body.get("minimum_directional_probability") != .63
            or body.get("brti_flip_veto_z") != .20
            or body.get("brti_flip_policy") != "brti-near-strike-z0.20-v1"):
        raise ValueError("frozen manifest integrity: unsupported policy or schema")
    return doc


def _bin() -> dict[str, Any]:
    return {"total": 0, "correct": 0, "wrong": 0, "accuracy": None}


def _count(bucket: dict[str, Any], correct: bool) -> None:
    bucket["total"] += 1
    bucket["correct" if correct else "wrong"] += 1


def _finish(bucket: dict[str, Any]) -> None:
    n = bucket["total"]
    bucket["accuracy"] = bucket["correct"] / n if n else None


def _skip_bin() -> dict[str, Any]:
    return {"total": 0, "counterfactual_correct": 0,
            "counterfactual_wrong": 0, "counterfactual_accuracy": None}


def _skip(bucket: dict[str, Any], correct: bool) -> None:
    bucket["total"] += 1
    bucket["counterfactual_correct" if correct
           else "counterfactual_wrong"] += 1


def _finish_skip(bucket: dict[str, Any]) -> None:
    n = bucket["total"]
    bucket["counterfactual_accuracy"] = (
        bucket["counterfactual_correct"] / n if n else None
    )


def _risk_category(value: object) -> str:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return "unavailable"
    if value <= COIN_FLIP_MAX_RISK_Z:
        return "near_strike"
    if value <= 1.:
        return "z_0_2_to_1"
    return "z_gt_1"


def _valid_probs(final: Any) -> bool:
    yes, no = final.p_yes, final.p_no
    return (
        type(yes) in (int, float) and type(no) in (int, float)
        and math.isfinite(yes) and math.isfinite(no)
        and 0 <= yes <= 1 and 0 <= no <= 1
        and abs(yes+no-1) <= 1e-6
        and (final.directional_probability is None or (
            type(final.directional_probability) in (int, float)
            and math.isfinite(final.directional_probability)
            and abs(final.directional_probability-max(yes,no)) <= 1e-6
        ))
    )


def evaluate_prospective_monitor(
    root: Path, *, now_ns: int,
) -> dict[str, Any]:
    """Score only first N NEW recorded checkpoints following frozen time.

    Previously inspected historical settlements cannot enter: the scheduled
    checkpoint must be strictly AFTER registration. Outcomes never rank or
    filter markets inside the recorded first-N horizon.
    """
    root=Path(root).resolve()
    if not (root/MANIFEST_FILE).exists():
        return {
            "status": "NOT_FROZEN",
            "instruction": "Run --freeze once before new 480s markets are issued",
            "trades_executed": False, "model_changed": False,
            "live_policy_changed": False,
        }
    doc=load_frozen_manifest(root)
    if type(now_ns) is not int or now_ns < doc["started_at_ns"]:
        raise ValueError("Current clock precedes prospective registration")
    excluded=Counter()
    eligible=[]
    for f in _forecasts(root):
        if f.scheduled_ts_ns <= doc["started_at_ns"]:
            excluded["before_freeze"] += 1
            continue
        if f.checkpoint_seconds != doc["checkpoint_seconds"]:
            excluded["non_480_checkpoint"] += 1
            continue
        if f.model_hash != doc["model_hash"]:
            excluded["different_model"] += 1
            continue
        if f.fusion_hash is not None:
            excluded["fusion_enabled"] += 1
            continue
        if (f.prediction_ts_ns < f.scheduled_ts_ns
                or f.prediction_ts_ns >
                f.scheduled_ts_ns + MAX_CHECKPOINT_LATENESS_NS):
            excluded["invalid_checkpoint_time"] += 1
            continue
        # Future checkpoints cannot enter a report before they occurred.
        if f.prediction_ts_ns > now_ns:
            excluded["future_checkpoint"] += 1
            continue
        eligible.append(f)
    eligible.sort(key=lambda f: (f.scheduled_ts_ns, f.market_ticker))
    first=eligible[:doc["fixed_horizon_recorded_markets"]]
    for key in ("before_freeze","different_model","fusion_enabled",
                "non_480_checkpoint","invalid_checkpoint_time",
                "future_checkpoint"):
        excluded[key] += 0

    issued=_bin()
    probability=_skip_bin()
    veto=_skip_bin()
    candidate=_bin()
    references={key: _bin() for key in _REFERENCE_CLASSES}
    risk={
        "near_strike": _bin(), "z_0_2_to_1": _bin(),
        "z_gt_1": _bin(), "unavailable": _bin(),
    }
    by_skip=Counter()
    settled_count=pending=unhealthy=bad_snapshot=policy_bad=model_scored=0
    score_labels=[]
    score_proba=[]
    evidence_problems=Counter()

    for f in first:
        close=_close_ns(f)
        if close >= now_ns:
            pending += 1
            continue
        try:
            outcome=_load_saved_settlement(root,f)
        except (SettlementIntegrityError,OSError,ValueError):
            evidence_problems["invalid_settlement"] += 1
            continue
        if outcome is None:
            pending += 1
            continue
        if (outcome.fetched_ts_ns <= close
                or outcome.fetched_ts_ns > now_ns):
            evidence_problems["inconsistent_settlement_timestamp"] += 1
            continue
        settled_count += 1
        if f.forecast=="SKIP":
            by_skip[f.reason or "UNSPECIFIED"] += 1
        if f.forecast=="SKIP" and f.reason not in (
            _PROBABILITY_SKIP,_FLIP_SKIP,
        ):
            unhealthy += 1
            continue
        if not _valid_probs(f):
            evidence_problems["invalid_probabilities"] += 1
            continue
        model_side="ABOVE" if f.p_yes>=.5 else "BELOW"
        correct=model_side==outcome.actual_side
        conf=max(f.p_yes,f.p_no)
        z=f.coin_flip_risk_z
        at_risk=(
            type(z) in (float,int) and math.isfinite(z)
            and 0 <= z <= doc["brti_flip_veto_z"]
        )
        if conf <= doc["minimum_directional_probability"]:
            expected="SKIP"
            expected_reason=_PROBABILITY_SKIP
        elif at_risk:
            expected="SKIP"
            expected_reason=_FLIP_SKIP
        else:
            expected=model_side
            expected_reason=None
        if (f.forecast!=expected or f.reason!=expected_reason):
            policy_bad += 1
            continue

        model_scored += 1
        score_labels.append(int(outcome.actual_side=="ABOVE"))
        score_proba.append(float(f.p_yes))
        if f.forecast in ("ABOVE","BELOW"):
            _count(issued, correct)
        elif f.reason==_PROBABILITY_SKIP:
            _skip(probability,correct)
        elif f.reason==_FLIP_SKIP:
            _skip(veto,correct)
        _count(risk[_risk_category(z)],correct)

        # Source timestamps and the SHA-256 binding of the exact original
        # final forecast are both verified before using reference features.
        try:
            load_forward_snapshot(root,f)
        except (OSError,ValueError):
            bad_snapshot += 1
            alignment="unavailable"
        else:
            alignment=_reference_alignment(root,f,model_side)
        _count(references[alignment],correct)

        if (f.reason==_PROBABILITY_SKIP
                and conf<=doc["minimum_directional_probability"]
                and type(z) in (int,float) and math.isfinite(z)
                and z>1. and alignment=="agree_with_model"):
            _count(candidate,correct)

    for b in (issued,candidate,*risk.values(),*references.values()):
        _finish(b)
    for b in (probability,veto):
        _finish_skip(b)
    metrics=None
    if score_labels:
        m=probability_metrics(score_labels,score_proba)
        metrics={"markets":len(score_labels),"brier":m.brier,
                 "log_loss":m.log_loss,"ece":m.ece,
                 "all_market_directional_accuracy":m.accuracy}

    required=doc["fixed_horizon_recorded_markets"]
    policy_drift=(
        MINIMUM_FINAL_DIRECTIONAL_PROBABILITY != doc["minimum_directional_probability"]
        or COIN_FLIP_MAX_RISK_Z != doc["brti_flip_veto_z"]
        or COIN_FLIP_POLICY != doc["brti_flip_policy"]
    )
    if policy_drift or policy_bad:
        status="POLICY_INCONSISTENCY"
    elif (len(first)==required and pending==0
          and not evidence_problems and settled_count==required):
        status="FROZEN_HORIZON_COMPLETE_DESCRIPTIVE"
    else:
        status="COLLECTING_NEW_SETTLEMENTS"
    operational_coverage=issued["total"]/settled_count if settled_count else None
    return {
        "status": status,
        "frozen_baseline": {
            key: doc[key] for key in (
                "model_hash","started_at_ns","sha256",
                "minimum_directional_probability","brti_flip_veto_z",
                "fixed_horizon_recorded_markets","primary_comparison",
            )
        },
        "recorded_post_freeze":len(first),
        "recorded_horizon":required,
        "pending_settlements":pending,
        "settled_post_freeze":settled_count,
        "issued":issued,
        "operational_coverage":operational_coverage,
        "probability_skips":probability,
        "flip_veto_skips":veto,
        "unhealthy_skips":unhealthy,
        "skip_reasons":dict(sorted(by_skip.items())),
        "policy_inconsistencies":policy_bad,
        "model_scored_settled":model_scored,
        "candidate_joint_under_live_gate":candidate,
        "reference_by_model_outcome":references,
        "risk_by_model_outcome":risk,
        "missing_or_invalid_causal_snapshots":bad_snapshot,
        "confidence_quality":metrics,
        "recorded_exclusions":dict(excluded),
        "evidence_problems":dict(evidence_problems),
        "model_changed":False,
        "live_policy_changed":False,
        "trades_executed":False,
        "live_config_drifts_from_freeze":policy_drift,
        "independent_of_previous_129_markets":True,
        "warning": (
            "PROSPECTIVE RECORDED-MARKET OBSERVATIONS ONLY. The first "
            "frozen-horizon timestamped 480s records were chosen BEFORE "
            "their labels, but a missing recorded market (app offline) is "
            "not included in coverage. Until all frozen markets are "
            "settled this is a descriptive progress report, not a "
            "statistically validated live-policy improvement. The frozen "
            "joint under-gate diagnostic NEVER unlocks AutoTrade or changes "
            "probabilities. Even at the full horizon assess sample size, "
            "risk, calibration, fills, and fees before changing production."
        ),
    }


def _pretty(report: dict[str, Any]) -> None:
    if report["status"]=="NOT_FROZEN":
        print("NO FROZEN PROSPECTIVE BASELINE: run --freeze once")
        return
    base=report["frozen_baseline"]
    print("KALSHIEDGE 480s | FROZEN PROSPECTIVE ERROR MONITOR")
    print("Status:",report["status"])
    print("Baseline SHA-256:",base["sha256"])
    print("Model:",base["model_hash"])
    print("Recorded:",report["recorded_post_freeze"],"/",report["recorded_horizon"],
          "| Settled:",report["settled_post_freeze"],
          "| Pending:",report["pending_settlements"])
    issued=report["issued"]
    print(f'ACTUALLY ISSUED: {issued["total"]}, correct={issued["correct"]}, '
          f'wrong={issued["wrong"]}, accuracy={issued["accuracy"]}')
    print("Operational coverage:",report["operational_coverage"])
    print("Probability SKIPs:",report["probability_skips"])
    print("Flip-veto SKIPs:",report["flip_veto_skips"])
    print("Reference versus model error:")
    for name, row in report["reference_by_model_outcome"].items():
        print(" ",name,row)
    print("Under-live-gate joint signal diagnostic:",
          report["candidate_joint_under_live_gate"])
    print("Missing hash-verified causal snapshots:",
          report["missing_or_invalid_causal_snapshots"])
    print("Policy inconsistencies:",report["policy_inconsistencies"])
    print("DESCRIPTIVE ONLY: no model fitting, live threshold or trade changes.")


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--model-hash",default=None,
                        help="For --freeze, optional exact model SHA-256; defaults to active model")
    parser.add_argument("--freeze",action="store_true",
                        help="One-time CURRENT-TIME registration; cannot be reset/backdated")
    parser.add_argument("--json",action="store_true",help="Print machine-readable JSON")
    args=parser.parse_args(argv)
    try:
        if args.freeze:
            sha=args.model_hash
            if sha is None:
                from .live import load_default_bundle
                sha=load_default_bundle(args.root).bundle_sha256
            result=freeze_prospective_monitor(
                args.root,model_hash=sha,now_ns=time.time_ns()
            )
        else:
            result=evaluate_prospective_monitor(args.root,now_ns=time.time_ns())
            if args.model_hash is not None and result["status"]!="NOT_FROZEN":
                if result["frozen_baseline"]["model_hash"]!=args.model_hash:
                    raise ValueError("Requested model hash differs from frozen baseline")
    except (OSError,ValueError,FileExistsError) as exc:
        print(json.dumps({
            "status":"REFUSED",
            "reason":str(exc),
            "baseline_reset":False,
            "trades_executed":False,
        }))
        return 2
    if args.json or args.freeze:
        print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    else:
        _pretty(result)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
