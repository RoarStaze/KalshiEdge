"""Fitted 480-second live forecast fusion from immutable, settled evidence.

This is a single optional production probability layer, not an exploratory
shadow or challenger. Without a sealed, independently qualified local
artifact the promoted calibrated model remains unchanged. Never assign
fixed hand-picked signal weights to real-money live probabilities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .final_forecast import (
    COIN_FLIP_MAX_RISK_Z, MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
    FINAL_CHECKPOINT_SECONDS,
)
from .metrics import probability_metrics

POLICY = "live-480-fitted-multisignal-v3"
FEATURE_NAMES = (
    "base_logit",
    "brti_distance_bps",
    "brti_basis_bps",
    "brti_signed_risk_z",
    "near_strike_reversal_bps",
    "btc_return_15s_bps",
    "btc_return_60s_bps",
    "btc_taker_buy_imbalance_60s",
    "signed_aggressor_volume_btc",
    "kalshi_yes_taker_imbalance_60s",
)
MIN_ELIGIBLE = 480
CAL_MARKETS = 80
EVAL_MARKETS = 100
EMBARGO_MARKETS = 8
ROOT = Path("models/fusion")


class FusionError(ValueError):
    """Missing, unauthenticated or insufficient evidence; no live activation."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _number(value: object, low: float, high: float) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and low <= value <= high


def build_signal_vector(
    model_p_yes: float, features: Mapping[str, float], *, risk_z: float | None,
) -> tuple[float, ...] | None:
    """Causal checkpoint-only signals; absent data never means a neutral zero.

    The 'large buys/sells' input here is signed *aggregate* Binance aggressor
    volume over 60 seconds, NOT individual whale trades or trader identity.
    A separate whaletrade stream would require its own verified history.
    """
    if not _number(model_p_yes, 0, 1) or not _number(risk_z, 0, math.inf):
        return None
    required = {
        "brti_distance_to_strike_bps": (-3000, 3000),
        "brti_binance_gap_bps": (-3000, 3000),
        "btc_return_15s": (-0.25, 0.25),
        "btc_return_60s": (-0.35, 0.35),
        "btc_return_15s_available": (1, 1),
        "btc_return_60s_available": (1, 1),
        "btc_taker_buy_imbalance_60s": (-1, 1),
        "btc_base_volume_60s": (0, 1_000_000),
        "kalshi_yes_taker_imbalance_60s": (-1, 1),
    }
    if any(not _number(features.get(name), *limits) for name, limits in required.items()):
        return None
    distance = float(features["brti_distance_to_strike_bps"])
    basis = float(features["brti_binance_gap_bps"])
    r15 = float(features["btc_return_15s"]) * 10000
    r60 = float(features["btc_return_60s"]) * 10000
    imbalance = float(features["btc_taker_buy_imbalance_60s"])
    volume = float(features["btc_base_volume_60s"])
    sign = 1 if distance > 0 else -1 if distance < 0 else 0
    z = float(risk_z)
    p = min(1 - 1e-5, max(1e-5, float(model_p_yes)))
    return (
        math.log(p / (1 - p)),
        max(-250., min(250., distance)),
        max(-200., min(200., basis)),
        sign * min(10., z),
        -sign * r15 / (1 + z),
        r15,
        r60,
        imbalance,
        max(-500., min(500., imbalance * volume)),
        float(features["kalshi_yes_taker_imbalance_60s"]),
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-min(value, 700)))
    e = math.exp(max(-700, value))
    return e / (1.0 + e)


class FusionModel(BaseModel):
    """Hash-bound numeric coefficients; no pickle or executable artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    policy: str
    model_hash: str
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    fit_markets: int = Field(ge=1)
    calibration_markets: int = Field(ge=1)
    evaluation_markets: int = Field(ge=1)
    evaluation: dict[str, float]
    training_through_ts_ns: int = Field(gt=0)
    sha256: str

    @model_validator(mode="after")
    def check_integrity(self) -> "FusionModel":
        if self.policy != POLICY or self.feature_names != FEATURE_NAMES:
            raise ValueError("unknown fusion policy or feature schema")
        if not (len(self.model_hash) == 64 and all(c in "0123456789abcdef" for c in self.model_hash)):
            raise ValueError("invalid promoted model hash")
        n = len(FEATURE_NAMES)
        if not all(len(arr) == n for arr in (self.means, self.scales, self.coefficients)):
            raise ValueError("fusion numeric feature width mismatch")
        if not all(math.isfinite(v) for arr in (self.means, self.scales, self.coefficients) for v in arr):
            raise ValueError("nonfinite fusion coefficients")
        if not all(1e-12 <= v < 1e8 for v in self.scales):
            raise ValueError("invalid fusion normalization scales")
        if not math.isfinite(self.intercept) or abs(self.intercept) > 100:
            raise ValueError("invalid fusion intercept")
        if any(not math.isfinite(v) for v in self.evaluation.values()):
            raise ValueError("invalid fusion evaluation evidence")
        if self.sha256 != _sha(self.model_dump(mode="json", exclude={"sha256"})):
            raise ValueError("fusion SHA-256 verification failed")
        return self

    @classmethod
    def build(cls, *, model_hash: str, means: Sequence[float], scales: Sequence[float],
              coefficients: Sequence[float], intercept: float,
              fit_markets: int, calibration_markets: int, evaluation_markets: int,
              evaluation: dict[str, float], training_through_ts_ns: int) -> "FusionModel":
        payload = dict(
            policy=POLICY, model_hash=model_hash, feature_names=FEATURE_NAMES,
            means=tuple(float(v) for v in means), scales=tuple(float(v) for v in scales),
            coefficients=tuple(float(v) for v in coefficients), intercept=float(intercept),
            fit_markets=fit_markets, calibration_markets=calibration_markets,
            evaluation_markets=evaluation_markets,
            evaluation={key: float(val) for key, val in evaluation.items()},
            training_through_ts_ns=training_through_ts_ns,
        )
        return cls(**payload, sha256=_sha(payload))

    def predict(self, vector: Sequence[float]) -> float:
        if len(vector) != len(FEATURE_NAMES) or any(not math.isfinite(v) for v in vector):
            raise FusionError("invalid live fusion feature vector")
        logit = self.intercept + sum(
            weight * ((value - mean) / scale)
            for value, weight, mean, scale in zip(
                vector, self.coefficients, self.means, self.scales
            )
        )
        return _sigmoid(logit)


def _path(root: Path, model_hash: str) -> Path:
    if len(model_hash) != 64 or any(ch not in "0123456789abcdef" for ch in model_hash):
        raise FusionError("invalid model hash for fusion lookup")
    return Path(root).resolve() / ROOT / f"{model_hash}.json"


def load_active_fusion(root: Path, model_hash: str) -> FusionModel | None:
    path = _path(root, model_hash)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 20_000:
            raise FusionError("oversized active fusion artifact")
        raw = path.read_bytes()
        parsed = json.loads(raw)
        if (isinstance(parsed, dict) and parsed.get("policy") in (
            "live-480-fitted-multisignal-v1",
            "live-480-fitted-multisignal-v2",
        )):
            raise FusionError(
                "legacy v1/v2 fusion artifact is not compatible with v3 validation; "
                "back up its models/fusion/<model_hash>.json file, move it out "
                "of the active models/fusion directory, and re-fit with the "
                "current full chronological holdout before reactivation"
            )
        model = FusionModel.model_validate_json(raw)
    except FusionError:
        raise
    except (OSError, ValueError) as exc:
        raise FusionError("active fusion artifact invalid; fail closed") from exc
    if model.model_hash != model_hash:
        raise FusionError("active fusion model identity mismatch")
    return model


def persist_fusion(root: Path, model: FusionModel) -> Path:
    model = FusionModel.model_validate(model.model_dump(mode="json"))
    path = _path(root, model.model_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive-create: no silent refits or replacement of a reviewed model.
    with path.open("xb") as stream:
        stream.write(_canonical(model.model_dump(mode="json")) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def summarize_decisions(
    probabilities: Sequence[float], actual_yes: Sequence[int],
    risks: Sequence[float],
) -> dict[str, float]:
    if len(probabilities) != len(actual_yes) or len(probabilities) != len(risks):
        raise FusionError("evaluation arrays have different lengths")
    if any(not _number(p, 0, 1) for p in probabilities):
        raise FusionError("nonfinite evaluation probability")
    if any(y not in (0, 1) for y in actual_yes):
        raise FusionError("invalid evaluation class")
    if any(not _number(z, 0, math.inf) for z in risks):
        raise FusionError("invalid flip risk")
    # Use the EXACT locked final-forecast decision threshold; the previous
    # v1 strength score (>=0.63, i.e. >=81.5% predicted-side probability)
    # could approve a fusion that underperforms at the actual >63% live gate.
    qual = [(p, y) for p, y, z in zip(probabilities, actual_yes, risks)
            if max(p, 1. - p) > MINIMUM_FINAL_DIRECTIONAL_PROBABILITY
            and z > COIN_FLIP_MAX_RISK_Z]
    correct = sum((p >= .5) == bool(y) for p, y in qual)
    return {"qualified": len(qual), "correct": correct,
            "coverage": len(qual) / len(probabilities) if probabilities else 0.,
            "accuracy": correct / len(qual) if qual else 0.}


@dataclass(frozen=True)
class _Example:
    ticker: str
    checkpoint_ts_ns: int
    fetched_ts_ns: int
    probability: float
    risk_z: float
    vector: tuple[float, ...]
    actual_yes: int


def _verified_examples(root: Path, model_hash: str) -> tuple[list[_Example], dict[str, Any]]:
    """Count exact reasons same-model settled checkpoint evidence cannot be used.

    Each final is counted under ONE exclusion reason. The caller gets aggregate
    statistics only; no ticker, settlement, or point-in-time data are rewritten.
    This also keeps valid z>100 risk observations which the original final
    forecast schema and other BRTI forensics already accept.
    """
    from .final_history import (
        _forecasts, _load_saved_settlement, _close_ns, SettlementIntegrityError,
    )
    from .forward_feature_evidence_480 import load_forward_snapshot

    reasons = {
        "snapshot_missing_or_invalid": 0,
        "snapshot_unavailable": 0,
        "settlement_missing": 0,
        "settlement_invalid": 0,
        "probability_missing_or_invalid": 0,
        "risk_missing_or_invalid": 0,
        "signal_inputs_missing_or_invalid": 0,
        "duplicate_ticker": 0,
    }
    counts: dict[str, Any] = {
        "same_model_finals": 0, "eligible": 0, "missing_or_invalid": 0,
        "exclusion_reasons": reasons,
    }
    rows: list[_Example] = []
    seen: set[str] = set()
    for final in _forecasts(root):
        if (final.model_hash != model_hash
                or final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS):
            continue
        counts["same_model_finals"] += 1
        try:
            snapshot = load_forward_snapshot(root, final)
        except (OSError, ValueError):
            reasons["snapshot_missing_or_invalid"] += 1
            continue
        if snapshot.status != "FEATURES_OK":
            reasons["snapshot_unavailable"] += 1
            continue
        try:
            settled = _load_saved_settlement(root, final)
        except (OSError, ValueError, SettlementIntegrityError):
            reasons["settlement_invalid"] += 1
            continue
        if settled is None:
            reasons["settlement_missing"] += 1
            continue
        if settled.fetched_ts_ns <= _close_ns(final):
            reasons["settlement_invalid"] += 1
            continue
        base_probability = (
            final.unfused_p_yes if final.unfused_p_yes is not None
            else final.p_yes
        )
        if (not _number(final.p_yes, 0, 1)
                or not _number(base_probability, 0, 1)):
            reasons["probability_missing_or_invalid"] += 1
            continue
        if not _number(final.coin_flip_risk_z, 0, math.inf):
            reasons["risk_missing_or_invalid"] += 1
            continue
        vector = build_signal_vector(
            float(base_probability), snapshot.features,
            risk_z=final.coin_flip_risk_z,
        )
        if vector is None:
            reasons["signal_inputs_missing_or_invalid"] += 1
            continue
        if snapshot.market_ticker in seen:
            reasons["duplicate_ticker"] += 1
            continue
        seen.add(snapshot.market_ticker)
        rows.append(_Example(
            ticker=final.market_ticker,
            checkpoint_ts_ns=snapshot.prediction_ts_ns,
            fetched_ts_ns=settled.fetched_ts_ns,
            probability=float(base_probability),
            risk_z=float(final.coin_flip_risk_z),
            vector=vector,
            actual_yes=int(settled.result == "yes"),
        ))
        counts["eligible"] += 1
    rows.sort(key=lambda item: (item.checkpoint_ts_ns, item.ticker))
    counts["missing_or_invalid"] = sum(reasons.values())
    if counts["eligible"] + counts["missing_or_invalid"] != counts["same_model_finals"]:
        raise FusionError("fusion readiness accounting violated")
    return rows, counts


def train_verified_fusion(root: Path, *, deploy: bool = False) -> dict[str, Any]:
    """Fixed chronological train/calibration/holdout; direct live activation only on pass.

    Fit and calibration examples have settlements fetched before the NEXT
    block's start, preventing labels from crossing the chronological boundary.
    A failed quality gate never writes a live artifact.
    """
    from .live import load_default_bundle
    root = Path(root).resolve()
    model_hash = load_default_bundle(root).bundle_sha256
    if model_hash is None:
        raise FusionError("no hash-verified active model")
    if load_active_fusion(root, model_hash) is not None:
        raise FusionError("existing active fusion is immutable; refusing to overwrite")
    rows, counts = _verified_examples(root, model_hash)
    result: dict[str, Any] = dict(
        policy=POLICY, model_hash=model_hash, **counts,
        minimum_eligible=MIN_ELIGIBLE, deployed=False, status="INSUFFICIENT_VERIFIED_DATA",
    )
    if len(rows) < MIN_ELIGIBLE:
        return result
    val = rows[-EVAL_MARKETS:]
    cal_start = len(rows) - EVAL_MARKETS - EMBARGO_MARKETS - CAL_MARKETS
    fit = [r for r in rows[:cal_start - EMBARGO_MARKETS]
           if r.fetched_ts_ns < rows[cal_start].checkpoint_ts_ns]
    cal = [r for r in rows[cal_start:cal_start + CAL_MARKETS]
           if r.fetched_ts_ns < val[0].checkpoint_ts_ns]
    if (len(fit) < 250 or len(cal) < 70 or
            {r.actual_yes for r in fit} != {0, 1} or
            {r.actual_yes for r in cal} != {0, 1}):
        result["status"] = "CHRONOLOGICAL_EVIDENCE_REFUSED"
        result["fit_markets"] = len(fit)
        result["calibration_markets"] = len(cal)
        return result
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from scipy.optimize import minimize_scalar

    scaler = StandardScaler().fit([r.vector for r in fit])
    x_fit = scaler.transform([r.vector for r in fit])
    estimator = LogisticRegression(C=.05, solver="lbfgs", max_iter=2000, random_state=0)
    estimator.fit(x_fit, [r.actual_yes for r in fit])
    weights = tuple(float(v) for v in estimator.coef_[0])
    if weights[0] <= 0:
        result["status"] = "UNRELIABLE_BASE_MODEL_COEFFICIENT"
        return result
    means = tuple(float(v) for v in scaler.mean_)
    scales = tuple(float(v) if v > 1e-12 else 1.0 for v in scaler.scale_)
    raw_intercept = float(estimator.intercept_[0])
    logits_cal = [
        raw_intercept + sum(w * ((x - m) / s)
                            for x, w, m, s in zip(r.vector, weights, means, scales))
        for r in cal
    ]
    y_cal = [r.actual_yes for r in cal]
    def cal_loss(offset: float) -> float:
        return sum(
            -y * math.log(max(1e-12, _sigmoid(logit + offset)))
            -(1-y) * math.log(max(1e-12, 1-_sigmoid(logit + offset)))
            for logit, y in zip(logits_cal, y_cal)
        ) / len(y_cal) + 0.002 * offset * offset
    optimized = minimize_scalar(cal_loss, bounds=(-1.5, 1.5), method="bounded",
                                options={"xatol": 1e-6})
    if not optimized.success:
        raise FusionError("calibration optimization failed")
    intercept = raw_intercept + float(optimized.x)
    raw_p = [r.probability for r in val]
    labels = [r.actual_yes for r in val]
    # Evaluate exactly the stored numeric coefficients, before any activation.
    candidate = FusionModel.build(
        model_hash=model_hash, means=means, scales=scales, coefficients=weights,
        intercept=intercept, fit_markets=len(fit), calibration_markets=len(cal),
        evaluation_markets=len(val), evaluation={"brier_improvement": 0.0},
        training_through_ts_ns=fit[-1].checkpoint_ts_ns,
    )
    fused_p = [candidate.predict(r.vector) for r in val]
    base_metrics = probability_metrics(labels, raw_p)
    new_metrics = probability_metrics(labels, fused_p)
    before = summarize_decisions(raw_p, labels, [r.risk_z for r in val])
    after = summarize_decisions(fused_p, labels, [r.risk_z for r in val])
    diff_brier = base_metrics.brier - new_metrics.brier
    diff_log = base_metrics.log_loss - new_metrics.log_loss
    result.update(
        status="HOLDOUT_NOT_BEATEN", fit_markets=len(fit),
        calibration_markets=len(cal), evaluation_markets=len(val),
        base_brier=base_metrics.brier, fused_brier=new_metrics.brier,
        base_log_loss=base_metrics.log_loss, fused_log_loss=new_metrics.log_loss,
        base_ece=base_metrics.ece, fused_ece=new_metrics.ece,
        base_gate=before, fused_gate=after, brier_improvement=diff_brier,
        logloss_improvement=diff_log,
    )
    # Both probability scores AND coverage/correct decisions must improve.
    accepted = (
        diff_brier >= 0.0001 and diff_log >= 0.0001
        and after["qualified"] >= before["qualified"]
        and after["correct"] >= before["correct"]
        and after["coverage"] >= before["coverage"]
        and after["accuracy"] >= before["accuracy"] - 0.005
    )
    if not accepted:
        return result
    result["status"] = "VERIFIED_480_HOLDOUT_PASS"
    if deploy:
        active_now = load_default_bundle(root)
        if active_now.bundle_sha256 != model_hash:
            raise FusionError("active model identity changed before activation")
        signed = candidate.model_copy(update={
            "evaluation": {"brier_improvement": diff_brier,
                           "logloss_improvement": diff_log,
                           "base_gate_coverage": before["coverage"],
                           "fused_gate_coverage": after["coverage"]},
        })
        model = FusionModel.build(
            model_hash=model_hash, means=means, scales=scales, coefficients=weights,
            intercept=intercept, fit_markets=len(fit), calibration_markets=len(cal),
            evaluation_markets=len(val), evaluation=signed.evaluation,
            training_through_ts_ns=fit[-1].checkpoint_ts_ns,
        )
        path = persist_fusion(root, model)
        result["deployed"] = True
        result["artifact_sha256"] = model.sha256
        result["artifact_path"] = str(path)
        result["status"] = "DEPLOYED_RESTART_PREDICTOR"
    return result


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "fit"))
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    args = parser.parse_args(argv)
    try:
        from .live import load_default_bundle
        model_hash = load_default_bundle(args.root).bundle_sha256
        if model_hash is None:
            raise FusionError("active model missing bundle hash")
        if args.command == "status":
            deployed = load_active_fusion(args.root, model_hash)
            _, counts = _verified_examples(args.root, model_hash)
            result = dict(policy=POLICY, model_hash=model_hash,
                          status="ACTIVE" if deployed else "NOT_ACTIVE",
                          deployed=bool(deployed), minimum_eligible=MIN_ELIGIBLE,
                          **counts)
            if deployed is not None:
                result["artifact_sha256"] = deployed.sha256
                result["evaluation"] = deployed.evaluation
        else:
            result = train_verified_fusion(args.root, deploy=True)
    except (FusionError, OSError, ValueError) as exc:
        result = {"status": "REFUSED", "reason": str(exc), "deployed": False}
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result.get("status") in ("ACTIVE", "NOT_ACTIVE", "DEPLOYED_RESTART_PREDICTOR") else 2


if __name__ == "__main__":
    raise SystemExit(main())
