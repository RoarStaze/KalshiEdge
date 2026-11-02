"""Post-final-lock, research-only prospective 480s specialist observations.

This module does NOT fit models, make a final decision, touch AutoTrade or
call an exchange. The immutable final forecast is persisted BEFORE it runs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .final_forecast import FINAL_CHECKPOINT_SECONDS, FinalForecastRecord
from .frozen_specialist_480 import score_frozen_specialist
from .types import FeatureRow

if TYPE_CHECKING:
    from .live import LiveFeatureState, LivePredictionRecord

NS = 1_000_000_000
_SAFE_TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
SHADOW_DIR = Path("research/480_specialist/live_shadow")
CONFIDENCE_GATE = .69


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


class Shadow480Record(BaseModel):
    """One fully timestamped, immutable specialist/incumbent checkpoint pair."""

    model_config = ConfigDict(frozen=True)

    market_ticker: str
    scheduled_ts_ns: int = Field(gt=0)
    prediction_ts_ns: int = Field(gt=0)
    market_close_ts_ns: int = Field(gt=0)
    frozen_at_ns: int = Field(gt=0)
    frozen_model_sha256: str
    incumbent_model_hash: str | None
    incumbent_p_yes: float | None = Field(default=None, ge=0, le=1)
    status: Literal["OK", "SKIP"]
    reason: str | None = None
    p_yes: float | None = Field(default=None, ge=0, le=1)
    gate_qualified: bool | None = None
    record_sha256: str

    @model_validator(mode="after")
    def _check_integrity(self) -> "Shadow480Record":
        if not _SAFE_TICKER.fullmatch(self.market_ticker):
            raise ValueError("invalid 480s shadow ticker")
        if self.market_close_ts_ns != self.scheduled_ts_ns + FINAL_CHECKPOINT_SECONDS * NS:
            raise ValueError("shadow market close does not match final checkpoint")
        if len(self.frozen_model_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.frozen_model_sha256
        ):
            raise ValueError("invalid frozen specialist hash")
        if self.status == "OK":
            if (self.p_yes is None or self.reason is not None
                    or self.prediction_ts_ns <= self.frozen_at_ns
                    or abs(self.prediction_ts_ns - self.scheduled_ts_ns) > NS
                    or self.gate_qualified != (2 * abs(self.p_yes - .5) >= CONFIDENCE_GATE)):
                raise ValueError("invalid qualified shadow probability")
        else:
            if self.reason is None or self.p_yes is not None or self.gate_qualified is not None:
                raise ValueError("skipped shadow must record reason and no probability")
        if self.record_sha256 != _digest(self.model_dump(
            mode="json", exclude={"record_sha256"},
        )):
            raise ValueError("shadow record hash integrity check failed")
        return self


def build_shadow_record(
    frozen: Mapping[str, Any],
    *,
    final: FinalForecastRecord,
    prediction: "LivePredictionRecord",
    row: FeatureRow | None,
) -> Shadow480Record:
    """Score ONLY the snapshot belonging to an already-locked final forecast."""
    timestamp = final.prediction_ts_ns
    reason: str | None = None
    probability: float | None = None
    if final.checkpoint_seconds != FINAL_CHECKPOINT_SECONDS:
        reason = "SHADOW_WRONG_CHECKPOINT_POLICY"
    elif (abs(timestamp - final.scheduled_ts_ns) > NS
          or timestamp <= frozen["frozen_at_ns"]):
        reason = "SHADOW_NOT_FUTURE_480S_CHECKPOINT"
    elif prediction.prediction_ts_ns != timestamp or prediction.market_ticker != final.market_ticker:
        reason = "SHADOW_PREDICTION_IDENTITY_MISMATCH"
    elif prediction.model_hash != final.model_hash:
        reason = "SHADOW_INCUMBENT_MODEL_MISMATCH"
    elif prediction.status != "OK" or not prediction.feed_quality.healthy:
        reason = prediction.reason or "SHADOW_UNHEALTHY_INCUMBENT_INPUT"
    elif row is None:
        reason = "SHADOW_FEATURE_ROW_UNAVAILABLE"
    elif row.checkpoint_ts_ns != timestamp or row.market_ticker != final.market_ticker:
        reason = "SHADOW_FEATURE_IDENTITY_MISMATCH"
    else:
        try:
            probability = score_frozen_specialist(frozen, row)
        except (ValueError, TypeError, KeyError, OverflowError):
            reason = "SHADOW_INELIGIBLE_OR_STALE_FEATURES"

    if probability is not None and not (math.isfinite(probability) and 0 <= probability <= 1):
        probability = None
        reason = "SHADOW_INVALID_PROBABILITY"
    payload = dict(
        market_ticker=final.market_ticker,
        scheduled_ts_ns=final.scheduled_ts_ns,
        prediction_ts_ns=timestamp,
        market_close_ts_ns=final.scheduled_ts_ns + FINAL_CHECKPOINT_SECONDS * NS,
        frozen_at_ns=int(frozen["frozen_at_ns"]),
        frozen_model_sha256=frozen["sha256"],
        incumbent_model_hash=final.model_hash,
        incumbent_p_yes=final.p_yes,
        status="OK" if probability is not None else "SKIP",
        reason=reason,
        p_yes=probability,
        gate_qualified=None if probability is None else (2*abs(probability-.5)>=CONFIDENCE_GATE),
    )
    return Shadow480Record(**payload, record_sha256=_digest(payload))


class Shadow480Writer:
    """Create-once JSON; duplicate same record is idempotent, mutation is refused."""

    def __init__(self, bootstrap_root: Path):
        self.root = Path(bootstrap_root).resolve()
        if self.root.name == "raw" and self.root.parent.name == "data":
            raise ValueError("research shadow cannot write to canonical raw data")

    def save(self, record: Shadow480Record) -> Path | None:
        # Re-validate integrity even if a Pydantic model was mutated externally.
        record = Shadow480Record.model_validate(record.model_dump(mode="json"))
        day = datetime.fromtimestamp(record.market_close_ts_ns/NS, tz=timezone.utc).date().isoformat()
        path = self.root / SHADOW_DIR / day / f"{record.market_ticker}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = _canonical(record.model_dump(mode="json")) + b"\n"
        try:
            with path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            existing = load_shadow_record(path)
            if existing != record:
                raise ValueError("conflicting immutable shadow record for market")
            return None
        return path


def load_shadow_record(path: Path) -> Shadow480Record:
    with Path(path).open("rb") as handle:
        blob = handle.read(10_001)
    if len(blob) > 10_000:
        raise ValueError("shadow JSON record exceeds maximum size")
    try:
        return Shadow480Record.model_validate_json(blob)
    except (ValueError, TypeError) as exc:
        raise ValueError("shadow record hash or structural integrity invalid") from exc


def capture_research_shadow(
    *,
    frozen: Mapping[str, Any],
    final: FinalForecastRecord,
    prediction: "LivePredictionRecord",
    state: "LiveFeatureState",
    writer: Shadow480Writer,
) -> Path | None:
    """Called only AFTER the authoritative final record has been saved."""
    row = None
    if prediction.status == "OK" and prediction.feed_quality.healthy:
        try:
            row = state.feature_row(final.prediction_ts_ns)
        except Exception:
            # A missing live feature is an explicit SKIP; never derive a
            # fallback or use settled outcomes for shadow inference.
            row = None
    snapshot = build_shadow_record(
        frozen, final=final, prediction=prediction, row=row,
    )
    return writer.save(snapshot)
