from __future__ import annotations

"""One immutable read-only ABOVE/BELOW/SKIP decision per KXBTC15M round.

A final decision uses only a prediction computed at the actual observation
time, never backdated to the scheduled 240-second checkpoint. A missed
deadline or any integrity/health failure fails closed to SKIP.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from .live_kalshi import LiveMarket

if TYPE_CHECKING:
    from .live import LivePredictionRecord


NS = 1_000_000_000
FINAL_CHECKPOINT_SECONDS = 240
MINIMUM_FINAL_CONFIDENCE = 0.69
MAX_CHECKPOINT_LATENESS_NS = 2 * NS
_SAFE_TICKER = re.compile(r"^[A-Z0-9-]+$")


class FinalForecastRecord(BaseModel):
    """A one-time audit record; no trading instruction or order intent."""

    model_config = ConfigDict(frozen=True)

    market_ticker: str
    strike: float = Field(gt=0)
    scheduled_ts_ns: int = Field(gt=0)
    prediction_ts_ns: int = Field(gt=0)
    seconds_remaining: float = Field(ge=0)
    forecast: Literal["ABOVE", "BELOW", "SKIP"]
    reason: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    directional_probability: float | None = Field(default=None, ge=0, le=1)
    p_yes: float | None = Field(default=None, ge=0, le=1)
    p_no: float | None = Field(default=None, ge=0, le=1)
    model_hash: str | None = None
    model_version: str | None = None


class FinalForecastGate:
    """Locks once per market ticker at the 240-second checkpoint."""

    def __init__(self) -> None:
        self._locked_tickers: set[str] = set()

    def evaluate(
        self,
        market: LiveMarket | None,
        *,
        now_ns: int,
        prediction: LivePredictionRecord | None,
    ) -> FinalForecastRecord | None:
        if market is None or market.ticker in self._locked_tickers:
            return None
        scheduled = market.close_ts_ns - FINAL_CHECKPOINT_SECONDS * NS
        if now_ns < scheduled:
            return None

        # Prevent duplicate decisions even when a feed reconnects and emits
        # another LiveMarket for exactly the same 15-minute ticker.
        self._locked_tickers.add(market.ticker)
        remaining = max(0.0, (market.close_ts_ns - now_ns) / NS)
        base = dict(
            market_ticker=market.ticker,
            strike=market.strike,
            scheduled_ts_ns=scheduled,
            prediction_ts_ns=now_ns,
            seconds_remaining=remaining,
            model_hash=None if prediction is None else prediction.model_hash,
            model_version=None if prediction is None else prediction.model_version,
        )

        if now_ns > scheduled + MAX_CHECKPOINT_LATENESS_NS:
            return FinalForecastRecord(**base, forecast="SKIP", reason="MISSED_240S_CHECKPOINT")
        if now_ns >= market.close_ts_ns:
            return FinalForecastRecord(**base, forecast="SKIP", reason="MARKET_ALREADY_CLOSED")

        if prediction is None or prediction.prediction_ts_ns != now_ns:
            return FinalForecastRecord(**base, forecast="SKIP", reason="MISSING_CHECKPOINT_PREDICTION")
        if prediction.market_ticker != market.ticker:
            return FinalForecastRecord(**base, forecast="SKIP", reason="PREDICTION_MARKET_MISMATCH")
        if prediction.status != "OK" or not prediction.feed_quality.healthy:
            return FinalForecastRecord(
                **base, forecast="SKIP",
                reason=prediction.reason or "UNHEALTHY_CHECKPOINT_INPUTS",
            )

        confidence = prediction.confidence
        p_yes = prediction.p_yes
        p_no = prediction.p_no
        side = prediction.predicted_side
        if (
            confidence is None or p_yes is None or p_no is None
            or side not in ("ABOVE", "BELOW")
        ):
            return FinalForecastRecord(**base, forecast="SKIP", reason="INCOMPLETE_MODEL_OUTPUT")

        directional_probability = p_yes if side == "ABOVE" else p_no
        fields = dict(
            confidence=confidence,
            directional_probability=directional_probability,
            p_yes=p_yes,
            p_no=p_no,
        )
        if confidence < MINIMUM_FINAL_CONFIDENCE:
            return FinalForecastRecord(
                **base, **fields, forecast="SKIP", reason="CONFIDENCE_BELOW_69_PERCENT",
            )

        return FinalForecastRecord(**base, **fields, forecast=side)


class FinalForecastWriter:
    """Save one immutable JSON record per market; exclusive-create deduplicates restarts."""

    def __init__(self, bootstrap_root: Path) -> None:
        root = bootstrap_root.resolve()
        if root.name == "raw" and root.parent.name == "data":
            raise ValueError("final forecasts cannot target canonical Phase 1 data/raw")
        self.root = root

    def save(self, record: FinalForecastRecord, *, market_close_ts_ns: int) -> Path | None:
        if not _SAFE_TICKER.fullmatch(record.market_ticker):
            raise ValueError("market ticker cannot be used as a safe record filename")
        if record.scheduled_ts_ns != market_close_ts_ns - FINAL_CHECKPOINT_SECONDS * NS:
            raise ValueError("final forecast checkpoint does not match market close")
        day = datetime.fromtimestamp(market_close_ts_ns / NS, tz=timezone.utc).date().isoformat()
        path = self.root / "final_forecasts" / day / f"{record.market_ticker}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(record.model_dump(mode="json"), handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            # Existing forecast remains immutable even after a process restart.
            return None
        return path
