"""Causal settlement-average challenger for future 480s BTC rounds.

Research only: no orders, incumbent prediction mutation, re-training, or
automatic promotion. Kalshi 15m BTC contracts compare BRTI minute averages.
This predeclared normal-log-diffusion approximation models the mean of the
60-second closing window, not the final single index tick.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .final_forecast import FINAL_CHECKPOINT_SECONDS, MAX_CHECKPOINT_LATENESS_NS, FinalForecastRecord
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts, _load_saved_settlement,
)
from .metrics import probability_metrics

if TYPE_CHECKING:
    from .live import LivePredictionRecord


NS = 1_000_000_000
WINDOW_SECONDS = 60
POLICY_ID = "brti-closing-minute-diffusion-v1"
SCHEMA = "KXBTC15M_480S_SETTLEMENT_SHADOW_V1"
SHADOW_DIR = Path("research/settlement_challenger_480")
CONFIDENCE_GATE = .69
SAFE_TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
SHA = re.compile(r"^[0-9a-f]{64}$")


def _positive(value: float, label: str) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite strictly positive number")
    return float(value)


def settlement_window_log_variance(
    sigma_per_sqrt_second: float, seconds_remaining: float, *,
    averaging_seconds: float = WINDOW_SECONDS,
) -> float:
    """Approximate log variance of the future closing-window arithmetic mean.

    Under zero-drift Brownian log-price approximation, E[average future log
    price] is current log index and Var[average] = sigma²*(T-2*w/3).
    The average of log-prices approximates log of the arithmetic index average;
    this is a falsifiable modeling assumption, NOT contract methodology.
    """
    sigma = _positive(sigma_per_sqrt_second, "BRTI volatility")
    remaining = _positive(seconds_remaining, "remaining seconds")
    window = _positive(averaging_seconds, "settlement window")
    if remaining < window:
        raise ValueError("forecast must precede the full 60-second closing window")
    variance = sigma * sigma * (remaining - 2.0 * window / 3.0)
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError("invalid settlement-window log variance")
    return variance


def settlement_window_probability(
    *, brti_value: float, strike: float,
    sigma_per_sqrt_second: float,
    seconds_remaining: float,
) -> float:
    """Research-only P(closing 60s BRTI average > verified opening strike).

    Uses the current, point-in-time BRTI and strictly causal realized sigma.
    Never use a later index revision or official result to reconstruct inputs.
    """
    brti = _positive(brti_value, "current BRTI")
    level = _positive(strike, "verified strike")
    variance = settlement_window_log_variance(
        sigma_per_sqrt_second, seconds_remaining,
    )
    z = math.log(brti / level) / math.sqrt(variance)
    result = 0.5 * math.erfc(-z / math.sqrt(2.0))
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("nonfinite settlement probability")
    return result


def _day(close_ns: int) -> str:
    return datetime.fromtimestamp(close_ns / NS, tz=timezone.utc).date().isoformat()


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class SettlementShadowRecord(BaseModel):
    """SHA-bound label-free output captured AFTER an immutable final forecast."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["KXBTC15M_480S_SETTLEMENT_SHADOW_V1"] = SCHEMA
    policy_id: Literal["brti-closing-minute-diffusion-v1"] = POLICY_ID
    market_ticker: str
    scheduled_ts_ns: int = Field(gt=0)
    prediction_ts_ns: int = Field(gt=0)
    market_close_ts_ns: int = Field(gt=0)
    final_sha256: str
    incumbent_model_hash: str | None = None
    incumbent_p_yes: float | None = Field(default=None, ge=0, le=1)
    strike: float = Field(gt=0)
    settlement_window_seconds: Literal[60] = WINDOW_SECONDS
    status: Literal["OK", "UNAVAILABLE"]
    reason: str | None = None
    brti_value: float | None = Field(default=None, gt=0)
    source_ts_ns: int | None = Field(default=None, gt=0)
    sigma_per_sqrt_second: float | None = Field(default=None, gt=0)
    sigma_observed_seconds: float | None = Field(default=None, gt=0)
    sigma_observation_count: int | None = Field(default=None, ge=31)
    p_yes: float | None = Field(default=None, ge=0, le=1)
    gate_qualified: bool | None = None
    record_sha256: str

    @model_validator(mode="after")
    def verify(self) -> "SettlementShadowRecord":
        if not SAFE_TICKER.fullmatch(self.market_ticker):
            raise ValueError("invalid settlement shadow ticker")
        if not SHA.fullmatch(self.final_sha256) or not SHA.fullmatch(self.record_sha256):
            raise ValueError("invalid settlement shadow SHA-256")
        if (self.market_close_ts_ns != self.scheduled_ts_ns
                + FINAL_CHECKPOINT_SECONDS * NS
                or not self.scheduled_ts_ns <= self.prediction_ts_ns
                <= self.scheduled_ts_ns + MAX_CHECKPOINT_LATENESS_NS):
            raise ValueError("not an on-time 480s settlement checkpoint")
        expected = _digest(self.model_dump(mode="json", exclude={"record_sha256"}))
        if self.record_sha256 != expected:
            raise ValueError("settlement shadow hash integrity failed")
        if self.status == "OK":
            if (self.reason is not None or any(x is None for x in (
                self.brti_value, self.source_ts_ns, self.sigma_per_sqrt_second,
                self.sigma_observed_seconds, self.sigma_observation_count, self.p_yes,
            ))):
                raise ValueError("available forecast requires causal BRTI and sigma")
            if (not 0 <= self.prediction_ts_ns - self.source_ts_ns <= 5 * NS
                    or self.sigma_observed_seconds < 60
                    or self.sigma_observation_count < 31):
                raise ValueError("BRTI evidence is stale, too short or from the future")
            modeled = settlement_window_probability(
                brti_value=self.brti_value, strike=self.strike,
                sigma_per_sqrt_second=self.sigma_per_sqrt_second,
                seconds_remaining=(self.market_close_ts_ns-self.prediction_ts_ns)/NS,
            )
            if abs(modeled - self.p_yes) > 1e-12:
                raise ValueError("probability does not reproduce declared policy")
            if self.gate_qualified != (2*abs(self.p_yes-.5) >= CONFIDENCE_GATE):
                raise ValueError("confidence-gate math does not match probability")
        elif (self.p_yes is not None or self.gate_qualified is not None
              or not self.reason or len(self.reason) > 150
              or any(x is not None for x in (
                  self.brti_value, self.source_ts_ns,
                  self.sigma_per_sqrt_second, self.sigma_observed_seconds,
                  self.sigma_observation_count,
              ))):
            raise ValueError("unavailable shadow must contain reason and no invented inputs")
        return self


def _final_digest(final: FinalForecastRecord, final_file: Path) -> str:
    path = Path(final_file)
    if (not SAFE_TICKER.fullmatch(final.market_ticker)
            or path.name != f"{final.market_ticker}.json"
            or path.parent.name != _day(_close_ns(final))
            or path.parent.parent.name != "final_forecasts"
            or final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS
            or not final.scheduled_ts_ns <= final.prediction_ts_ns
            <= final.scheduled_ts_ns + MAX_CHECKPOINT_LATENESS_NS):
        raise ValueError("settlement shadow must reference exact saved 480s final")
    raw = path.read_bytes()
    if FinalForecastRecord.model_validate_json(raw) != final:
        raise ValueError("saved immutable final does not match challenger input")
    return hashlib.sha256(raw).hexdigest()


def build_settlement_shadow(
    final: FinalForecastRecord,
    prediction: "LivePredictionRecord",
    final_file: Path,
) -> SettlementShadowRecord:
    """Create a standalone post-final research record with no outcome labels."""
    final_hash = _final_digest(final, final_file)
    reason: str | None = None
    source: tuple[float, int, float, float, int] | None = None
    p_yes: float | None = None
    if (prediction.market_ticker != final.market_ticker
            or prediction.prediction_ts_ns != final.prediction_ts_ns
            or prediction.model_hash != final.model_hash):
        reason = "PREDICTION_IDENTITY_MISMATCH"
    elif prediction.status != "OK" or not prediction.feed_quality.healthy:
        reason = prediction.reason or "UNHEALTHY_CHECKPOINT_INPUT"
    else:
        value = prediction.brti_value
        timestamp = prediction.brti_source_ts_ns
        sigma = prediction.brti_shadow_risk_sigma
        observed = prediction.brti_shadow_risk_observed_seconds
        points = prediction.brti_shadow_risk_observation_count
        if (type(timestamp) is not int
                or not 0 <= final.prediction_ts_ns - timestamp <= 5 * NS):
            reason = "MISSING_STALE_OR_FUTURE_BRTI"
        elif (type(observed) not in (int,float)
              or not math.isfinite(observed) or observed < 60
              or type(points) is not int or points < 31):
            reason = "INSUFFICIENT_CAUSAL_BRTI_HISTORY"
        else:
            try:
                p_yes = settlement_window_probability(
                    brti_value=value,
                    strike=final.strike,
                    sigma_per_sqrt_second=sigma,
                    seconds_remaining=(
                        _close_ns(final) - final.prediction_ts_ns
                    ) / NS,
                )
                source = (float(value), timestamp, float(sigma),
                          float(observed), points)
            except (TypeError, ValueError, OverflowError):
                reason = "MISSING_OR_INVALID_CAUSAL_BRTI_VOLATILITY"

    fields: dict[str, Any] = {
        "brti_value": None,
        "source_ts_ns": None,
        "sigma_per_sqrt_second": None,
        "sigma_observed_seconds": None,
        "sigma_observation_count": None,
    }
    if source is not None:
        fields.update({
            "brti_value": source[0],
            "source_ts_ns": source[1],
            "sigma_per_sqrt_second": source[2],
            "sigma_observed_seconds": source[3],
            "sigma_observation_count": source[4],
        })
    payload = {
        "schema_version": SCHEMA,
        "policy_id": POLICY_ID,
        "market_ticker": final.market_ticker,
        "scheduled_ts_ns": final.scheduled_ts_ns,
        "prediction_ts_ns": final.prediction_ts_ns,
        "market_close_ts_ns": _close_ns(final),
        "final_sha256": final_hash,
        "incumbent_model_hash": final.model_hash,
        "incumbent_p_yes": final.p_yes,
        "strike": final.strike,
        "settlement_window_seconds": WINDOW_SECONDS,
        "status": "OK" if p_yes is not None else "UNAVAILABLE",
        "reason": reason,
        "p_yes": p_yes,
        "gate_qualified": (2 * abs(p_yes-.5) >= CONFIDENCE_GATE)
                          if p_yes is not None else None,
        **fields,
    }
    return SettlementShadowRecord(
        **payload, record_sha256=_digest(payload),
    )


class SettlementShadowWriter:
    """Exclusive-create, immutable prospectively recorded model outputs."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if self.root.name == "raw" and self.root.parent.name == "data":
            raise ValueError("research artifacts cannot target canonical raw data")

    def save(self, record: SettlementShadowRecord) -> Path | None:
        record = SettlementShadowRecord.model_validate(record.model_dump(mode="json"))
        path = (
            self.root / SHADOW_DIR / _day(record.market_close_ts_ns)
            / f"{record.market_ticker}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = _canonical(record.model_dump(mode="json")) + b"\n"
        try:
            with path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            prior = SettlementShadowRecord.model_validate_json(path.read_bytes())
            if prior != record:
                raise ValueError("conflicting immutable settlement shadow")
            return None
        return path


def load_settlement_shadow(
    root: Path, final: FinalForecastRecord,
) -> SettlementShadowRecord:
    root = Path(root).resolve()
    path = root / SHADOW_DIR / _day(_close_ns(final)) / f"{final.market_ticker}.json"
    with path.open("rb") as handle:
        raw = handle.read(10_001)
    if len(raw) > 10_000:
        raise ValueError("oversized settlement shadow record")
    record = SettlementShadowRecord.model_validate_json(raw)
    if (record.market_ticker != final.market_ticker
            or record.prediction_ts_ns != final.prediction_ts_ns
            or record.scheduled_ts_ns != final.scheduled_ts_ns
            or record.market_close_ts_ns != _close_ns(final)
            or record.strike != final.strike
            or record.incumbent_p_yes != final.p_yes
            or record.incumbent_model_hash != final.model_hash
            or record.final_sha256 != _final_digest(
                final, root / "final_forecasts" / _day(_close_ns(final))
                / f"{final.market_ticker}.json",
            )):
        raise ValueError("settlement shadow is not bound to unchanged final forecast")
    return record


def capture_settlement_shadow(
    *, final: FinalForecastRecord,
    prediction: "LivePredictionRecord",
    final_file: Path,
    writer: SettlementShadowWriter,
) -> Path | None:
    """Only after final_writer.save; never affects decisions or AutoTrade."""
    record = build_settlement_shadow(final, prediction, final_file)
    return writer.save(record)


def _gate(labels: list[int], probs: list[float], vetoes: list[bool]) -> dict[str, Any]:
    count = 0
    correct = 0
    for label, p, veto in zip(labels, probs, vetoes):
        if veto or 2*abs(p-.5) < CONFIDENCE_GATE:
            continue
        count += 1
        correct += int((p > .5) == bool(label))
    total = len(labels)
    return {
        "qualified": count, "correct": correct,
        "coverage": count/total if total else None,
        "qualified_accuracy": correct/count if count else None,
    }


def audit_settlement_challenger(root: Path) -> dict[str, Any]:
    """Evaluate only identical, officially settled, hash-verified pairs.

    Independent evidence collection/selection must predate outcomes.
    Missing evidence is reported, not filled in retrospectively.
    """
    root = Path(root).resolve()
    counts: Counter[str] = Counter()
    labels: list[int] = []
    candidate_p: list[float] = []
    incumbent_p: list[float] = []
    vetoes: list[bool] = []
    issued = 0
    issued_correct = 0
    for final in _forecasts(root):
        if final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
            counts["legacy_excluded"] += 1
            continue
        counts["total_480_finals"] += 1
        try:
            settlement = _load_saved_settlement(root, final)
        except (ValueError, OSError, SettlementIntegrityError):
            counts["invalid_settlement"] += 1
            continue
        if settlement is None:
            counts["pending_settlement"] += 1
            continue
        counts["settled_480"] += 1
        try:
            record = load_settlement_shadow(root, final)
        except FileNotFoundError:
            counts["missing_shadow_settled"] += 1
            continue
        except (ValueError, OSError):
            counts["invalid_shadow_settled"] += 1
            continue
        if record.status != "OK":
            counts["unavailable_shadow_settled"] += 1
            continue
        if (final.p_yes is None or not math.isfinite(final.p_yes)
                or record.p_yes is None):
            counts["missing_incumbent_probability"] += 1
            continue
        label = int(settlement.actual_side == "ABOVE")
        counts["paired_settled"] += 1
        labels.append(label)
        candidate_p.append(record.p_yes)
        incumbent_p.append(final.p_yes)
        vetoes.append(final.coin_flip_assessment == "HIGH")
        if final.forecast in ("ABOVE", "BELOW"):
            issued += 1
            issued_correct += int((final.forecast == "ABOVE") == bool(label))

    result: dict[str, Any] = {
        "report": "KALSHIEDGE_SETTLEMENT_WINDOW_CHALLENGER_480_V1",
        "status": "RESEARCH_ONLY",
        "policy_id": POLICY_ID,
        "live_model_changed": False,
        "autotrade_changed": False,
        "verified_profitability": False,
        "counts": {
            **dict(counts),
            **{name: counts[name] for name in (
                "total_480_finals", "settled_480", "paired_settled",
                "pending_settlement", "missing_shadow_settled",
                "invalid_shadow_settled", "unavailable_shadow_settled",
            )},
        },
        "incumbent_metrics": {"brier": None, "log_loss": None, "ece": None},
        "challenger_metrics": {"brier": None, "log_loss": None, "ece": None},
        "incumbent_issued": {
            "issued": issued, "correct": issued_correct,
            "qualified_accuracy": issued_correct/issued if issued else None,
            "coverage_on_paired": issued/len(labels) if labels else None,
        },
        "incumbent_raw_69_gate": _gate(labels, incumbent_p, vetoes),
        "challenger_69_gate_with_existing_coinflip_veto":
            _gate(labels, candidate_p, vetoes),
        "note": (
            "The 60-second settlement-average probability is an unfitted "
            "zero-drift analytic approximation. One matched market is one "
            "official outcome; no counterfactual fill, calibration or "
            "profitability is implied. Never auto-promote this challenger."
        ),
    }
    if labels:
        incumbent = probability_metrics(labels, incumbent_p)
        candidate = probability_metrics(labels, candidate_p)
        result["incumbent_metrics"] = incumbent.model_dump(mode="json")
        result["challenger_metrics"] = candidate.model_dump(mode="json")
        result["brier_improvement"] = incumbent.brier-candidate.brier
        result["log_loss_improvement"] = incumbent.log_loss-candidate.log_loss
    else:
        result["brier_improvement"] = None
        result["log_loss_improvement"] = None
    return result


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(
        description="Read-only official settlement challenger scorecard",
    )
    parser.add_argument("--root",type=Path,default=None)
    args=parser.parse_args(argv)
    if args.root is None:
        from .config import BootstrapSettings
        root=BootstrapSettings().bootstrap_dir
    else:
        root=args.root
    print(json.dumps(audit_settlement_challenger(root),sort_keys=True,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
