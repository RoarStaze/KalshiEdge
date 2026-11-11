"""Prospective read-only adaptive ensemble of two 480s settlement probabilities.

A blend is fitted at prediction time exclusively from already-fetched
official outcomes and hash-verified original research shadows. This module
never changes the promoted live model, forecast policy, or AutoTrade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .final_forecast import FINAL_CHECKPOINT_SECONDS, FinalForecastRecord
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts, _load_saved_settlement,
)
from .metrics import probability_metrics
from .settlement_challenger_480 import (
    CONFIDENCE_GATE, _final_digest, load_settlement_shadow,
)

NS = 1_000_000_000
SCHEMA = "KXBTC15M_480S_ADAPTIVE_ENSEMBLE_V1"
POLICY = "walkforward-brier-blend-v1"
OUT_DIR = Path("research/adaptive_ensemble_480")
MIN_TRAIN = 80
MAX_TRAIN = 300
MIN_REVIEW = 200
MIN_REVIEW_DAYS = 7
WEIGHTS = (0.0, .25, .5, .75, 1.0)
SHRINKS = (1.0, .75)
SHA = re.compile(r"^[0-9a-f]{64}$")
TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _day(ns: int) -> str:
    return datetime.fromtimestamp(ns / NS, timezone.utc).date().isoformat()


def _probability(value: Any) -> bool:
    return (type(value) in (int, float)
            and math.isfinite(value) and 0.0 <= value <= 1.0)


def blend_probability(incumbent: float, challenger: float,
                      weight: float, shrink: float) -> float:
    if not (_probability(incumbent) and _probability(challenger)
            and weight in WEIGHTS and shrink in SHRINKS):
        raise ValueError("invalid ensemble inputs or parameter grid")
    raw = (1.0 - weight) * incumbent + weight * challenger
    return .5 + shrink * (raw - .5)


@dataclass(frozen=True)
class TrainingPair:
    ticker: str
    fetched_ts_ns: int
    final_sha256: str
    challenger_sha256: str
    incumbent_p_yes: float
    challenger_p_yes: float
    actual_yes: int


def prior_training_pairs(root: Path, current: FinalForecastRecord) -> list[TrainingPair]:
    """Only labels verifiably fetched BEFORE the target prediction may fit it."""
    root = Path(root).resolve()
    if current.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
        return []
    pairs: list[TrainingPair] = []
    for final in _forecasts(root):
        if (final.market_ticker == current.market_ticker
                or final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS
                or _close_ns(final) >= current.prediction_ts_ns
                or final.prediction_ts_ns >= current.prediction_ts_ns
                or not _probability(final.p_yes)):
            continue
        try:
            settlement = _load_saved_settlement(root, final)
            if settlement is None:
                continue
            if not (_close_ns(final) < settlement.fetched_ts_ns
                    < current.prediction_ts_ns):
                continue
            shadow = load_settlement_shadow(root, final)
            if shadow.status != "OK" or not _probability(shadow.p_yes):
                continue
            pairs.append(TrainingPair(
                ticker=final.market_ticker,
                fetched_ts_ns=settlement.fetched_ts_ns,
                final_sha256=shadow.final_sha256,
                challenger_sha256=shadow.record_sha256,
                incumbent_p_yes=float(final.p_yes),
                challenger_p_yes=float(shadow.p_yes),
                actual_yes=int(settlement.actual_side == "ABOVE"),
            ))
        except (OSError, ValueError, SettlementIntegrityError):
            # Corrupted, missing or mismatched prior evidence is NEVER fitted.
            continue
    pairs.sort(key=lambda pair: (pair.fetched_ts_ns, pair.ticker))
    return pairs[-MAX_TRAIN:]


def _training_hash(pairs: Sequence[TrainingPair]) -> str:
    return _digest([asdict(pair) for pair in pairs])


def select_blend(observations: Sequence[tuple[float, float, int]]) -> tuple[float, float]:
    """Fit on *past* outcomes only; keep a deterministic incumbent tie preference.

    Tiny complexity penalty discourages fitting tiny apparent improvements in
    challenger weight or unnecessary shrink. No model is promoted here.
    """
    if not observations:
        raise ValueError("cannot fit a blend without historical observations")
    for incumbent, challenger, label in observations:
        if not _probability(incumbent) or not _probability(challenger) or type(label) is not int or label not in (0, 1):
            raise ValueError("invalid historical probability or label")
    best: tuple[float, float, float, float] | None = None
    for weight in WEIGHTS:
        for shrink in SHRINKS:
            loss = sum(
                (blend_probability(incumbent, challenger, weight, shrink)-label)**2
                for incumbent, challenger, label in observations
            ) / len(observations)
            score = (loss + .0001*weight + .0001*(1-shrink),
                     weight, -shrink, loss)
            if best is None or score < best:
                best = score
                selected = (weight, shrink)
    return selected


class AdaptiveEnsembleRecord(BaseModel):
    """Immutable label-free ensemble forecast tied to existing final+shadow."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["KXBTC15M_480S_ADAPTIVE_ENSEMBLE_V1"] = SCHEMA
    policy_id: Literal["walkforward-brier-blend-v1"] = POLICY
    market_ticker: str
    prediction_ts_ns: int = Field(gt=0)
    market_close_ts_ns: int = Field(gt=0)
    final_sha256: str
    challenger_sha256: str | None = None
    incumbent_p_yes: float | None = Field(default=None, ge=0, le=1)
    challenger_p_yes: float | None = Field(default=None, ge=0, le=1)
    training_count: int = Field(ge=0, le=MAX_TRAIN)
    training_sha256: str
    status: Literal["READY", "UNTRAINED", "UNAVAILABLE"]
    reason: str | None = None
    weight: float | None = None
    shrink: float | None = None
    p_yes: float | None = Field(default=None, ge=0, le=1)
    gate_qualified: bool | None = None
    record_sha256: str

    @model_validator(mode="after")
    def verify(self) -> "AdaptiveEnsembleRecord":
        if not TICKER.fullmatch(self.market_ticker):
            raise ValueError("invalid ensemble ticker")
        if any(not SHA.fullmatch(h) for h in (
            self.final_sha256, self.training_sha256, self.record_sha256
        )):
            raise ValueError("invalid ensemble SHA-256")
        if self.challenger_sha256 is not None and not SHA.fullmatch(self.challenger_sha256):
            raise ValueError("invalid challenger SHA-256")
        if self.record_sha256 != _digest(self.model_dump(mode="json",exclude={"record_sha256"})):
            raise ValueError("ensemble record hash integrity failure")
        if self.status == "READY":
            if (self.reason is not None or self.training_count < MIN_TRAIN
                    or self.challenger_sha256 is None
                    or not _probability(self.incumbent_p_yes)
                    or not _probability(self.challenger_p_yes)
                    or self.weight not in WEIGHTS or self.shrink not in SHRINKS
                    or not _probability(self.p_yes)):
                raise ValueError("ready ensemble lacks valid provenance or fit")
            expected = blend_probability(
                self.incumbent_p_yes, self.challenger_p_yes, self.weight, self.shrink,
            )
            if (abs(self.p_yes-expected) > 1e-12
                    or self.gate_qualified != (2*abs(self.p_yes-.5) >= CONFIDENCE_GATE)):
                raise ValueError("ensemble probability or gate does not reproduce policy")
        else:
            if (not self.reason or self.weight is not None
                    or self.shrink is not None or self.p_yes is not None
                    or self.gate_qualified is not None):
                raise ValueError("non-ready ensemble may not invent a prediction")
            if self.status == "UNTRAINED" and self.training_count >= MIN_TRAIN:
                raise ValueError("untrained ensemble contains sufficient history")
        return self


def _record_path(root: Path, final: FinalForecastRecord) -> Path:
    if not TICKER.fullmatch(final.market_ticker):
        raise ValueError("unsafe ensemble market ticker")
    return (Path(root).resolve() / OUT_DIR / _day(_close_ns(final))
            / f"{final.market_ticker}.json")


def build_adaptive_ensemble(root: Path, final: FinalForecastRecord) -> AdaptiveEnsembleRecord:
    """Build once at the checkpoint, using only verified earlier known outcomes."""
    root = Path(root).resolve()
    final_file = (root / "final_forecasts" / _day(_close_ns(final))
                  / f"{final.market_ticker}.json")
    final_sha = _final_digest(final, final_file)
    shadow = None
    try:
        shadow = load_settlement_shadow(root, final)
    except (OSError, ValueError):
        pass
    input_ok = (shadow is not None and shadow.status == "OK"
                and _probability(final.p_yes) and _probability(shadow.p_yes))
    pairs = prior_training_pairs(root, final) if input_ok else []
    if not input_ok:
        status = "UNAVAILABLE"
        reason = "MISSING_OR_UNUSABLE_SOURCE"
    elif len(pairs) < MIN_TRAIN:
        status = "UNTRAINED"
        reason = "INSUFFICIENT_PRIOR_SETTLEMENTS"
    else:
        status = "READY"
        reason = None
    weight = shrink = probability = qualified = None
    if status == "READY":
        weight, shrink = select_blend([
            (item.incumbent_p_yes, item.challenger_p_yes, item.actual_yes)
            for item in pairs
        ])
        probability = blend_probability(final.p_yes, shadow.p_yes, weight, shrink)
        qualified = 2*abs(probability-.5) >= CONFIDENCE_GATE
    payload = dict(
        schema_version=SCHEMA, policy_id=POLICY,
        market_ticker=final.market_ticker,
        prediction_ts_ns=final.prediction_ts_ns,
        market_close_ts_ns=_close_ns(final),
        final_sha256=final_sha,
        challenger_sha256=shadow.record_sha256 if shadow is not None else None,
        incumbent_p_yes=final.p_yes,
        challenger_p_yes=shadow.p_yes if shadow is not None else None,
        training_count=len(pairs), training_sha256=_training_hash(pairs),
        status=status, reason=reason,
        weight=weight, shrink=shrink, p_yes=probability,
        gate_qualified=qualified,
    )
    return AdaptiveEnsembleRecord(**payload,record_sha256=_digest(payload))


class AdaptiveEnsembleWriter:
    """Create-once research output; never overwrite prior forecasts."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if self.root.name == "raw" and self.root.parent.name == "data":
            raise ValueError("research cannot target canonical raw directory")

    def save(self, record: AdaptiveEnsembleRecord) -> Path | None:
        record = AdaptiveEnsembleRecord.model_validate(record.model_dump(mode="json"))
        path = (self.root / OUT_DIR / _day(record.market_close_ts_ns)
                / f"{record.market_ticker}.json")
        path.parent.mkdir(parents=True,exist_ok=True)
        data = _canonical(record.model_dump(mode="json")) + b"\n"
        try:
            with path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            existing = AdaptiveEnsembleRecord.model_validate_json(path.read_bytes())
            if existing != record:
                raise ValueError("conflicting immutable adaptive ensemble")
            return None
        return path


def load_adaptive_ensemble(root: Path, final: FinalForecastRecord) -> AdaptiveEnsembleRecord:
    """Verify source bindings AND the originally knowable training cohort."""
    root = Path(root).resolve()
    path = _record_path(root, final)
    with path.open("rb") as handle:
        raw = handle.read(10_001)
    if len(raw) > 10_000:
        raise ValueError("oversized adaptive ensemble record")
    record = AdaptiveEnsembleRecord.model_validate_json(raw)
    final_path = (root/"final_forecasts"/_day(_close_ns(final))
                  /f"{final.market_ticker}.json")
    if (record.market_ticker != final.market_ticker
            or record.prediction_ts_ns != final.prediction_ts_ns
            or record.market_close_ts_ns != _close_ns(final)
            or record.final_sha256 != _final_digest(final,final_path)
            or record.incumbent_p_yes != final.p_yes):
        raise ValueError("ensemble does not match immutable original final")
    if record.challenger_sha256 is not None:
        shadow = load_settlement_shadow(root,final)
        if (shadow.record_sha256 != record.challenger_sha256
                or shadow.p_yes != record.challenger_p_yes):
            raise ValueError("ensemble challenger source was changed")
    if record.status != "UNAVAILABLE":
        pairs = prior_training_pairs(root,final)
        if (len(pairs) != record.training_count
                or _training_hash(pairs) != record.training_sha256):
            raise ValueError("ensemble training cohort integrity failure")
    return record


def capture_adaptive_ensemble(
    *, root: Path, final: FinalForecastRecord, writer: AdaptiveEnsembleWriter,
) -> Path | None:
    return writer.save(build_adaptive_ensemble(root,final))


def _gate(labels: Sequence[int], probs: Sequence[float],
          vetoes: Sequence[bool]) -> dict[str, Any]:
    qualified = correct = 0
    for label, probability, veto in zip(labels,probs,vetoes):
        if veto or 2*abs(probability-.5) < CONFIDENCE_GATE:
            continue
        qualified += 1
        correct += int((probability > .5) == bool(label))
    return dict(
        qualified=qualified, correct=correct,
        coverage=qualified/len(labels) if labels else None,
        qualified_accuracy=correct/qualified if qualified else None,
    )


def audit_adaptive_ensemble(root: Path) -> dict[str, Any]:
    """Read-only, same-cohort evaluation of SAVED prospective ensemble output."""
    root = Path(root).resolve()
    counts: Counter[str] = Counter()
    labels: list[int] = []
    incumbent: list[float] = []
    challenger: list[float] = []
    vetoes: list[bool] = []
    days: set[str] = set()
    for final in _forecasts(root):
        if final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
            counts["legacy_excluded"] += 1
            continue
        counts["total_480_finals"] += 1
        try:
            settlement = _load_saved_settlement(root, final)
        except (OSError, ValueError, SettlementIntegrityError):
            counts["invalid_settlement"] += 1
            continue
        if settlement is None:
            counts["pending_settlement"] += 1
            continue
        counts["settled_480"] += 1
        try:
            record = load_adaptive_ensemble(root,final)
        except FileNotFoundError:
            counts["missing_ensemble_settled"] += 1
            continue
        except (OSError, ValueError):
            counts["invalid_ensemble_settled"] += 1
            continue
        if record.status == "UNTRAINED":
            counts["untrained_ensemble_settled"] += 1
            continue
        if record.status != "READY":
            counts["unavailable_ensemble_settled"] += 1
            continue
        if not (_probability(record.p_yes) and _probability(final.p_yes)):
            counts["invalid_ensemble_settled"] += 1
            continue
        counts["paired_settled_ready"] += 1
        days.add(_day(_close_ns(final)))
        labels.append(int(settlement.actual_side=="ABOVE"))
        incumbent.append(final.p_yes)
        challenger.append(record.p_yes)
        vetoes.append(final.coin_flip_assessment == "HIGH")

    incumbent_metrics = probability_metrics(labels, incumbent).model_dump(mode="json") if labels else None
    ensemble_metrics = probability_metrics(labels, challenger).model_dump(mode="json") if labels else None
    incumbent_gate = _gate(labels,incumbent,vetoes)
    ensemble_gate = _gate(labels,challenger,vetoes)
    failures: list[str] = []
    if len(labels) < MIN_REVIEW:
        failures.append("INSUFFICIENT_MATCHED_FORWARD_MARKETS")
    if len(days) < MIN_REVIEW_DAYS:
        failures.append("INSUFFICIENT_INDEPENDENT_UTC_DAYS")
    if not labels or not (ensemble_metrics["brier"] < incumbent_metrics["brier"]
                           and ensemble_metrics["log_loss"] < incumbent_metrics["log_loss"]):
        failures.append("NO_STRICT_PROBABILITY_SCORE_IMPROVEMENT")
    if not labels or not (ensemble_gate["coverage"] > incumbent_gate["coverage"]):
        failures.append("NO_STRICT_COVERAGE_GAIN")
    if (ensemble_gate["qualified_accuracy"] is None
            or incumbent_gate["qualified_accuracy"] is None
            or not ensemble_gate["qualified_accuracy"] > incumbent_gate["qualified_accuracy"]):
        failures.append("NO_STRICT_QUALIFIED_ACCURACY_GAIN")

    return {
        "report": "KALSHIEDGE_ADAPTIVE_SETTLEMENT_ENSEMBLE_480_V1",
        "status": "RESEARCH_ONLY",
        "policy_id": POLICY,
        "live_model_changed": False,
        "autotrade_changed": False,
        "verified_profitability": False,
        "counts": {**dict(counts),**{name:counts[name] for name in (
            "total_480_finals","settled_480","paired_settled_ready",
            "pending_settlement","missing_ensemble_settled",
            "invalid_ensemble_settled","untrained_ensemble_settled",
            "unavailable_ensemble_settled",
        )}},
        "independent_utc_days": len(days),
        "incumbent_metrics": incumbent_metrics,
        "ensemble_metrics": ensemble_metrics,
        "incumbent_raw_69_gate_with_coinflip_veto": incumbent_gate,
        "ensemble_69_gate_with_coinflip_veto": ensemble_gate,
        "promotion_review": {
            "eligible": not failures,
            "screening_failures": failures,
            "automatic_promotion": False,
            "minimum_paired_markets": MIN_REVIEW,
            "minimum_utc_days": MIN_REVIEW_DAYS,
        },
        "note": (
            "This is an adaptive research shadow, not a production model. "
            "Historical labels were filtered by the fetched timestamp at each "
            "forecast. Evaluation uses separately saved future forecasts. "
            "Passing review screens does not verify liquidity, fills, trading "
            "costs, net returns, or external prospective replication."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only adaptive ensemble 480s scorecard")
    parser.add_argument("--root",type=Path,default=None)
    args = parser.parse_args(argv)
    if args.root is None:
        from .config import BootstrapSettings
        root = BootstrapSettings().bootstrap_dir
    else:
        root = args.root
    print(json.dumps(audit_adaptive_ensemble(root),sort_keys=True,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
