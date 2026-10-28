from __future__ import annotations

"""Immutable correct-and-confident refresher cases from official Kalshi results.

These are never included in the mistake pool. Only the one-in-four refresher
round may use them for model fitting, and only in its development partition.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .final_forecast import MAX_CHECKPOINT_LATENESS_NS, MINIMUM_FINAL_CONFIDENCE, FinalForecastRecord
from .final_history import (
    OfficialSettlement, SettlementIntegrityError, SAFE_TICKER,
    _close_ns, _forecasts, _load_saved_settlement, _settlement_path,
)
from .mistake_learning import MistakePoolError, _forecast_path, _sha256_file

LOG = logging.getLogger(__name__)
NS = 1_000_000_000
POLICY = "official-settled-confident-correct-refresher-v1"
MINIMUM_DIRECTIONAL_PROBABILITY = .69


class CorrectCase(BaseModel):
    model_config = ConfigDict(frozen=True)
    policy: Literal["official-settled-confident-correct-refresher-v1"] = POLICY
    market_ticker: str
    market_close_ts_ns: int = Field(gt=0)
    forecast: Literal["ABOVE", "BELOW"]
    actual_side: Literal["ABOVE", "BELOW"]
    confidence: float = Field(ge=MINIMUM_FINAL_CONFIDENCE, le=1)
    directional_probability: float = Field(ge=MINIMUM_DIRECTIONAL_PROBABILITY, le=1)
    selected_label: Literal["yes", "no"]
    model_hash: str
    model_version: str
    forecast_sha256: str
    settlement_sha256: str


def _correct_path(root: Path, ticker: str, close_ns: int) -> Path:
    if not SAFE_TICKER.fullmatch(ticker):
        raise MistakePoolError("unsafe correct-refresher ticker")
    day = datetime.fromtimestamp(close_ns / NS, timezone.utc).date().isoformat()
    return root / "correct_pool" / day / f"{ticker}.json"


def _expected_correct(root: Path, forecast: FinalForecastRecord, official: OfficialSettlement) -> CorrectCase | None:
    if forecast.market_ticker != official.market_ticker or _close_ns(forecast) != official.market_close_ts_ns:
        raise MistakePoolError("correct example market mismatch")
    if official.source != "kalshi-public-market-api" or official.market_status not in {"settled", "finalized"}:
        raise MistakePoolError("correct example requires official terminal settlement")
    if official.result not in ("yes", "no") or official.actual_side != ("ABOVE" if official.result == "yes" else "BELOW"):
        raise MistakePoolError("correct example settlement inconsistent")
    if forecast.forecast not in ("ABOVE", "BELOW") or forecast.forecast != official.actual_side:
        return None
    if not forecast.scheduled_ts_ns <= forecast.prediction_ts_ns <= forecast.scheduled_ts_ns + MAX_CHECKPOINT_LATENESS_NS:
        return None
    if (
        forecast.confidence is None or forecast.confidence < MINIMUM_FINAL_CONFIDENCE
        or forecast.directional_probability is None or forecast.directional_probability < MINIMUM_DIRECTIONAL_PROBABILITY
    ):
        return None
    if forecast.model_hash is None or len(forecast.model_hash) != 64 or any(x not in "0123456789abcdef" for x in forecast.model_hash.lower()):
        raise MistakePoolError("correct example lacks model SHA")
    if not forecast.model_version:
        raise MistakePoolError("correct example lacks model version")
    original = _forecast_path(root, forecast.market_ticker, _close_ns(forecast))
    settled = _settlement_path(root, forecast)
    if not original.is_file() or not settled.is_file():
        raise MistakePoolError("correct example source missing")
    return CorrectCase(
        market_ticker=forecast.market_ticker,
        market_close_ts_ns=_close_ns(forecast),
        forecast=forecast.forecast,
        actual_side=official.actual_side,
        confidence=forecast.confidence,
        directional_probability=forecast.directional_probability,
        selected_label=official.result,
        model_hash=forecast.model_hash.lower(),
        model_version=forecast.model_version,
        forecast_sha256=_sha256_file(original),
        settlement_sha256=_sha256_file(settled),
    )


def _verified_correct(root: Path, path: Path) -> CorrectCase:
    try:
        case = CorrectCase.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MistakePoolError(f"invalid refresher case {path.name}") from exc
    if path.resolve() != _correct_path(root, case.market_ticker, case.market_close_ts_ns).resolve():
        raise MistakePoolError("refresher case path mismatch")
    try:
        original = _forecast_path(root, case.market_ticker, case.market_close_ts_ns)
        forecast = FinalForecastRecord.model_validate_json(original.read_text(encoding="utf-8"))
        official = _load_saved_settlement(root, forecast)
    except (OSError, ValueError, SettlementIntegrityError) as exc:
        raise MistakePoolError("correct refresher source corrupted") from exc
    if official is None:
        raise MistakePoolError("correct refresher source has no official settlement")
    expected = _expected_correct(root, forecast, official)
    if expected is None or expected != case:
        raise MistakePoolError("correct refresher source no longer matches case")
    return case


def enroll_correct_forecasts(root: Path) -> dict[str, int]:
    root = root.resolve()
    stats = {"eligible": 0, "new_cases": 0, "already_enrolled": 0, "pending": 0, "errors": 0}
    for forecast in _forecasts(root):
        try:
            official = _load_saved_settlement(root, forecast)
            if official is None:
                stats["pending"] += 1
                continue
            eligible = _expected_correct(root, forecast, official)
            path = _correct_path(root, forecast.market_ticker, _close_ns(forecast))
            if eligible is None:
                if path.is_file():
                    _verified_correct(root, path)
                    raise MistakePoolError("previously enrolled refresher case became ineligible")
                continue
            stats["eligible"] += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8", newline="\n") as handle:
                    json.dump(eligible.model_dump(mode="json"), handle, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                stats["new_cases"] += 1
            except FileExistsError:
                _verified_correct(root, path)
                stats["already_enrolled"] += 1
        except (MistakePoolError, SettlementIntegrityError, OSError, ValueError) as exc:
            stats["errors"] += 1
            LOG.error("Correct case enrollment failed for %s: %s", forecast.market_ticker, exc)
    return stats


def verified_correct_pool(
    root: Path, *, allowed_tickers: set[str] | None = None,
) -> tuple[tuple[CorrectCase, Path, str], ...]:
    root = root.resolve()
    result: list[tuple[CorrectCase, Path, str]] = []
    seen: set[str] = set()
    for path in sorted((root / "correct_pool").glob("*/*.json")):
        # Eligibility is determined from historical market metadata BEFORE
        # loading any case from calibration/lockbox timestamps.
        if allowed_tickers is not None and path.stem not in allowed_tickers:
            continue
        case = _verified_correct(root, path)
        if case.market_ticker in seen:
            raise MistakePoolError("duplicate correct refresher ticker")
        seen.add(case.market_ticker)
        result.append((case, path, _sha256_file(path)))
    return tuple(result)
