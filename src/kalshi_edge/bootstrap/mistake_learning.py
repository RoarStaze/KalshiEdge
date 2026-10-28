from __future__ import annotations

"""Point-in-time mistake-focused enrollment based exclusively on FINAL settled calls.

Only officially settled WRONG directional decisions or calibrated low-confidence
decisions enter the *future training-candidate pool*. Correct, confident markets
never enter. Raw historical model features are NOT derived from outcome metadata.

The pool is separate from the immutable forecasts and from untouched evaluation
lockboxes. The provenance fields bind each enrollment to both source artifacts.
"""

import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .final_forecast import FINAL_CHECKPOINT_SECONDS, MAX_CHECKPOINT_LATENESS_NS, FinalForecastRecord
from .final_history import (
    OfficialSettlement,
    SettlementIntegrityError,
    _close_ns,
    _forecasts,
    _load_saved_settlement,
    _settlement_path,
    SAFE_TICKER,
)

LOG = logging.getLogger(__name__)
NS = 1_000_000_000
POLICY_ID = "settled-final-mistakes-and-low-confidence-v1"
LOW_CONFIDENCE_THRESHOLD = 0.69
LOW_DIRECTIONAL_PROBABILITY_THRESHOLD = 0.69
CaseReason = Literal["INCORRECT_FINAL", "LOW_CONFIDENCE", "LOW_DIRECTIONAL_PROBABILITY"]


class MistakePoolError(ValueError):
    """An example was inconsistent with its official source artifacts."""


class MistakeCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy: Literal["settled-final-mistakes-and-low-confidence-v1"] = POLICY_ID
    market_ticker: str
    market_close_ts_ns: int = Field(gt=0)
    forecast: Literal["ABOVE", "BELOW", "SKIP"]
    actual_side: Literal["ABOVE", "BELOW"]
    reasons: tuple[CaseReason, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    directional_probability: float | None = Field(default=None, ge=0, le=1)
    model_hash: str
    model_version: str
    forecast_sha256: str
    settlement_sha256: str
    selected_label: Literal["yes", "no"]

    @property
    def is_incorrect(self) -> bool:
        return "INCORRECT_FINAL" in self.reasons


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_path(root: Path, ticker: str, close_ns: int) -> Path:
    if not SAFE_TICKER.fullmatch(ticker):
        raise MistakePoolError("invalid ticker for mistake pool path")
    day = datetime.fromtimestamp(close_ns / NS, timezone.utc).date().isoformat()
    return root / "mistake_pool" / day / f"{ticker}.json"


def _forecast_path(root: Path, ticker: str, close_ns: int) -> Path:
    if not SAFE_TICKER.fullmatch(ticker):
        raise MistakePoolError("invalid ticker for original forecast path")
    day = datetime.fromtimestamp(close_ns / NS, timezone.utc).date().isoformat()
    return root / "final_forecasts" / day / f"{ticker}.json"


def _reasons(forecast: FinalForecastRecord, settlement: OfficialSettlement) -> tuple[CaseReason, ...]:
    """Strictly score only the immutable final call, never a live preview."""

    if forecast.market_ticker != settlement.market_ticker or _close_ns(forecast) != settlement.market_close_ts_ns:
        raise MistakePoolError("mismatched official market for forecast")
    if settlement.result not in {"yes", "no"}:
        raise MistakePoolError("cannot enroll an unsettled market")
    if settlement.actual_side != ("ABOVE" if settlement.result == "yes" else "BELOW"):
        raise MistakePoolError("official settlement result is inconsistent")
    if settlement.market_status not in {"settled", "finalized"} or settlement.source != "kalshi-public-market-api":
        raise MistakePoolError("enrollment requires an authoritative terminal Kalshi outcome")
    if not (forecast.scheduled_ts_ns <= forecast.prediction_ts_ns <= forecast.scheduled_ts_ns + MAX_CHECKPOINT_LATENESS_NS):
        # Late/missed checkpoint forecasts must NEVER become a training label.
        return ()

    reasons: list[CaseReason] = []
    if forecast.forecast in ("ABOVE", "BELOW") and forecast.forecast != settlement.actual_side:
        reasons.append("INCORRECT_FINAL")

    confidence = forecast.confidence
    probability = forecast.directional_probability
    if confidence is not None and math.isfinite(confidence) and confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("LOW_CONFIDENCE")
    if probability is not None and math.isfinite(probability) and probability < LOW_DIRECTIONAL_PROBABILITY_THRESHOLD:
        reasons.append("LOW_DIRECTIONAL_PROBABILITY")

    # An unhealthy/late SKIP is not a difficult market with a numerical
    # predictive estimate. Only real low-confidence predictions qualify.
    if forecast.forecast == "SKIP" and forecast.reason != "CONFIDENCE_BELOW_69_PERCENT":
        return ()
    return tuple(reasons)


def _expected_case(root: Path, forecast: FinalForecastRecord, settlement: OfficialSettlement) -> MistakeCase | None:
    reasons = _reasons(forecast, settlement)
    if not reasons:
        return None
    if not forecast.model_hash or len(forecast.model_hash) != 64 or any(x not in "0123456789abcdef" for x in forecast.model_hash.lower()):
        raise MistakePoolError("forecast lacks a valid SHA-256 model identity")
    if not forecast.model_version:
        raise MistakePoolError("forecast lacks a versioned model identity")

    forecast_path = _forecast_path(root, forecast.market_ticker, _close_ns(forecast))
    settlement_file = _settlement_path(root, forecast)
    if not forecast_path.is_file() or not settlement_file.is_file():
        raise MistakePoolError("source forecast/official settlement files are missing")
    return MistakeCase(
        market_ticker=forecast.market_ticker,
        market_close_ts_ns=_close_ns(forecast),
        forecast=forecast.forecast,
        actual_side=settlement.actual_side,
        reasons=reasons,
        confidence=forecast.confidence,
        directional_probability=forecast.directional_probability,
        model_hash=forecast.model_hash.lower(),
        model_version=forecast.model_version,
        forecast_sha256=_sha256_file(forecast_path),
        settlement_sha256=_sha256_file(settlement_file),
        selected_label=settlement.result,
    )


def _load_case(root: Path, path: Path) -> MistakeCase:
    """Verify the case against immutable sources, including its selection rule."""

    try:
        case = MistakeCase.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MistakePoolError(f"unreadable mistake case {path.name}") from exc
    if path.resolve() != _case_path(root, case.market_ticker, case.market_close_ts_ns).resolve():
        raise MistakePoolError("mistake case path/ticker/close mismatch")

    forecast_file = _forecast_path(root, case.market_ticker, case.market_close_ts_ns)
    try:
        forecast = FinalForecastRecord.model_validate_json(forecast_file.read_text(encoding="utf-8"))
        settlement = _load_saved_settlement(root, forecast)
    except (OSError, ValueError, SettlementIntegrityError) as exc:
        raise MistakePoolError("mistake case source artifacts failed verification") from exc
    if settlement is None:
        raise MistakePoolError("mistake case has no official settlement")

    expected = _expected_case(root, forecast, settlement)
    if expected is None or expected != case:
        raise MistakePoolError("mistake case no longer matches official source/policy")
    return case


def enroll_settled_forecasts(root: Path) -> dict[str, int]:
    """Append immutable eligible cases; refuse to enroll uncertain/unsettled data."""

    root = root.resolve()
    counts = {
        "settled_checked": 0,
        "eligible": 0,
        "new_cases": 0,
        "already_enrolled": 0,
        "correct_high_confidence_excluded": 0,
        "awaiting_settlement": 0,
        "source_errors": 0,
    }
    for forecast in _forecasts(root):
        try:
            settlement = _load_saved_settlement(root, forecast)
            if settlement is None:
                counts["awaiting_settlement"] += 1
                continue
            counts["settled_checked"] += 1
            candidate = _expected_case(root, forecast, settlement)
            if candidate is None:
                counts["correct_high_confidence_excluded"] += 1
                continue

            counts["eligible"] += 1
            path = _case_path(root, candidate.market_ticker, candidate.market_close_ts_ns)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8", newline="\n") as handle:
                    json.dump(candidate.model_dump(mode="json"), handle, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                counts["new_cases"] += 1
            except FileExistsError:
                _load_case(root, path)
                counts["already_enrolled"] += 1
        except (MistakePoolError, SettlementIntegrityError, OSError, ValueError) as exc:
            counts["source_errors"] += 1
            LOG.error("Cannot enroll %s: %s", forecast.market_ticker, exc)
    return counts


def verified_mistake_pool(
    root: Path, *, allowed_tickers: set[str] | None = None,
) -> tuple[tuple[MistakeCase, Path, str], ...]:
    """Strongly verify eligible candidate sources and provide immutable hashes."""

    root = root.resolve()
    result: list[tuple[MistakeCase, Path, str]] = []
    found: set[str] = set()
    for path in sorted((root / "mistake_pool").glob("*/*.json")):
        # IMPORTANT: filtering filenames BEFORE loading cases prevents the training
        # path from ever inspecting outcomes of calibration or lockbox markets.
        if allowed_tickers is not None and path.stem not in allowed_tickers:
            continue
        case = _load_case(root, path)
        if case.market_ticker in found:
            raise MistakePoolError("duplicate mistake case for one market")
        found.add(case.market_ticker)
        result.append((case, path, _sha256_file(path)))
    return tuple(result)
