from __future__ import annotations

"""Read-only live dashboard projection of immutable final records and live JSONL.

Never extrapolate a stale prediction into a current market signal. The browser
derives countdowns from published observation and close timestamps only.
"""

import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .final_forecast import FINAL_CHECKPOINT_SECONDS, FinalForecastRecord
from .live import LivePredictionRecord

LOG = logging.getLogger(__name__)
NS = 1_000_000_000
MAX_LIVE_AGE_NS = 8 * NS
TAIL_BYTES = 512 * 1024


def _latest_prediction(root: Path, *, now_ns: int) -> LivePredictionRecord | None:
    base = root / "predictions"
    # UTC date folder lookup avoids scanning the entire historical prediction store.
    utc_now = datetime.fromtimestamp(now_ns / NS, timezone.utc)
    for delta in range(2):
        day = (utc_now - timedelta(days=delta)).date().isoformat()
        path = base / day / "predictions.jsonl"
        if not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                end = stream.tell()
                stream.seek(max(0, end - TAIL_BYTES))
                chunk = stream.read()
            # If the tail begins in the middle of a record, discard its first line.
            if end > TAIL_BYTES:
                chunk = chunk.partition(b"\n")[2]
            for line in reversed(chunk.splitlines()):
                try:
                    record = LivePredictionRecord.model_validate_json(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if 0 <= now_ns - record.prediction_ts_ns <= MAX_LIVE_AGE_NS:
                    return record
                if record.prediction_ts_ns < now_ns - MAX_LIVE_AGE_NS:
                    return None
        except OSError as exc:
            LOG.warning("Unable to read latest prediction from %s: %s", path, exc)
    return None


def _current_final(root: Path, ticker: str, close_ts_ns: int) -> FinalForecastRecord | None:
    day = datetime.fromtimestamp(close_ts_ns / NS, timezone.utc).date().isoformat()
    # Do not let untrusted ticker values reach filesystem paths.
    if not ticker.startswith("KXBTC15M-") or not ticker.replace("-", "").isalnum():
        return None
    path = root / "final_forecasts" / day / f"{ticker}.json"
    if not path.is_file():
        return None
    try:
        record = FinalForecastRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            record.market_ticker != ticker
            or abs(record.scheduled_ts_ns + FINAL_CHECKPOINT_SECONDS * NS - close_ts_ns) > 2_000_000
        ):
            LOG.warning("Invalid final record linkage for %s", path)
            return None
        return record
    except (OSError, ValueError) as exc:
        LOG.warning("Unable to read immutable final forecast %s: %s", path, exc)
        return None


def read_live_dashboard(root: Path, *, now_ns: int | None = None) -> dict[str, Any]:
    """Return only recent observed live data and a verified matching final record."""

    now_ns = time.time_ns() if now_ns is None else now_ns
    result: dict[str, Any] = {
        "status": "WAITING_FOR_PREDICTOR",
        "server_time_ns": now_ns,
        "market_ticker": None,
        "market_close_ts_ns": None,
        "checkpoint_ts_ns": None,
        "seconds_remaining": None,
        "strike": None,
        "latest_prediction": None,
        "final_forecast": None,
        "minimum_confidence": 0.69,
    }
    prediction = _latest_prediction(root.resolve(), now_ns=now_ns)
    if prediction is None:
        return result

    result["status"] = "WAITING_FOR_MARKET"
    if prediction.market_ticker is None or prediction.seconds_remaining is None:
        return result

    seconds_remaining = prediction.seconds_remaining
    if not math.isfinite(seconds_remaining) or seconds_remaining <= 0:
        return result

    close_ns = prediction.prediction_ts_ns + round(seconds_remaining * NS)
    if close_ns <= now_ns:
        return result

    final = _current_final(root.resolve(), prediction.market_ticker, close_ns)
    if final is not None:
        # The JSONL seconds_remaining field is a float; use the exact sealed
        # checkpoint timestamp after a tolerance-checked match.
        close_ns = final.scheduled_ts_ns + FINAL_CHECKPOINT_SECONDS * NS
    result.update(
        {
            "status": "LIVE" if prediction.status == "OK" and prediction.feed_quality.healthy else "DEGRADED",
            "market_ticker": prediction.market_ticker,
            "market_close_ts_ns": close_ns,
            "checkpoint_ts_ns": close_ns - FINAL_CHECKPOINT_SECONDS * NS,
            "seconds_remaining": (close_ns - now_ns) / NS,
            "strike": prediction.strike,
            "latest_prediction": prediction.model_dump(mode="json"),
            "final_forecast": None if final is None else final.model_dump(mode="json"),
        }
    )
    return result
