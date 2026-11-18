"""Offline chronological replay of read-only intramarket early-flip alerts.

The ONLY label is an observed subsequent Kalshi YES midpoint crossing 50c,
not an executable round-trip profit, a settled forecast, or causally validated
trading edge. Synthetic/per-market results are descriptive research, never a
live model promotion or order authorization.

Usage:
    python -m kalshi_edge.bootstrap.flip_replay \
        data/bootstrap/predictions/2026-11-18/predictions.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Any

from .live import LivePredictionRecord
from .reversal_monitor import NS, _quote_mid, assess_reversal_risk

MAX_GAP_NS = 4 * NS
WINDOW_NS = 90 * NS


def _wilson(hits: int, total: int) -> tuple[float | None, float | None]:
    if not total:
        return None, None
    z = 1.959963984540054
    p = hits / total
    den = 1 + z*z/total
    center = (p + z*z/(2*total))/den
    width = z*math.sqrt((p*(1-p)+z*z/(4*total))/total)/den
    return max(0.,center-width), min(1.,center+width)


def evaluate_prediction_stream(
    path: Path, *, horizon_seconds: int = 15,
) -> dict[str, Any]:
    """Replay monotonically observed 1Hz predictions and score later quotes.

    A warning contributes one episode per market/side per 30 seconds. Every
    episode must have complete future observations to horizon expiry; gaps,
    file end, degraded model quality and market rollover are unscored.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not 1 <= horizon_seconds <= 60:
        raise ValueError("replay horizon must be 1..60 seconds")
    horizon_ns = horizon_seconds * NS
    recent: deque[dict[str, Any]] = deque()
    pending: list[dict[str, Any]] = []
    last_event: dict[str, int] = {}
    last_ticker = None
    last_epoch = None
    last_ts = None
    eligible_ticks = alerts = hits = misses = unresolved = invalid_lines = 0
    leads: list[float] = []

    def abandon():
        nonlocal unresolved
        unresolved += len(pending)
        pending.clear()

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = LivePredictionRecord.model_validate_json(line)
            except (ValueError, UnicodeError):
                invalid_lines += 1
                abandon()
                recent.clear()
                last_ts = None
                continue
            ts = item.prediction_ts_ns
            ticker = item.market_ticker
            epoch = item.market_stream_epoch
            if not ticker or item.status != "OK" or not item.feed_quality.healthy:
                abandon()
                recent.clear()
                last_ts = None
                continue
            # A disconnected, nonmonotonic or cross-market stream is not a
            # continuous observation of the future target event.
            if (last_ticker != ticker or last_epoch != epoch or
                    (last_ts is not None
                     and not 0 < ts-last_ts <= MAX_GAP_NS)):
                abandon()
                recent.clear()
                last_event.clear()
            last_ticker, last_epoch, last_ts = ticker, epoch, ts
            record = item.model_dump(mode="json")
            mid = _quote_mid(record)
            if mid is None:
                abandon()
                recent.clear()
                continue

            for alert in list(pending):
                if ts > alert["at"] and ts <= alert["deadline"]:
                    crossed = mid <= .5 if alert["side"] == "DOWN" else mid >= .5
                    if crossed and alert["first_cross"] is None:
                        alert["first_cross"] = ts
                if ts >= alert["deadline"]:
                    if alert["first_cross"] is not None:
                        hits += 1
                        leads.append((alert["first_cross"]-alert["at"])/NS)
                    else:
                        misses += 1
                    pending.remove(alert)

            recent.append(record)
            while recent and ts - recent[0]["prediction_ts_ns"] > WINDOW_NS:
                recent.popleft()
            signal = assess_reversal_risk(list(recent), now_ns=ts, market_ticker=ticker)
            if signal["level"] == "UNAVAILABLE":
                continue
            eligible_ticks += 1
            warning = signal.get("flip_alert")
            if warning is None:
                continue
            side = warning["direction"]
            # Avoid treating 1 Hz refreshes as 15 independent alerts.
            last = last_event.get(side)
            if last is not None and ts - last < max(30 * NS, horizon_ns):
                continue
            last_event[side] = ts
            alerts += 1
            pending.append({
                "side": side, "at": ts, "deadline": ts + horizon_ns,
                "first_cross": None,
            })
    abandon()
    resolved = hits + misses
    lo, hi = _wilson(hits, resolved)
    return {
        "analysis_status": "DESCRIPTIVE_ONLY",
        "label_type": "FUTURE_KALSHI_QUOTE_MID_CROSS_50C",
        "label_warning": "Crossing of a Kalshi market quote is not a profitable executable trade or a verified BTC settlement reversal.",
        "horizon_seconds": horizon_seconds,
        "alerts": alerts,
        "resolved": resolved,
        "hits": hits,
        "misses": misses,
        "unresolved": unresolved,
        "eligible_ticks": eligible_ticks,
        "invalid_lines": invalid_lines,
        "precision": hits / resolved if resolved else None,
        "wilson_lower_95": lo,
        "wilson_upper_95": hi,
        "mean_lead_seconds": sum(leads)/len(leads) if leads else None,
        "profitable_trade_probability": None,
        "live_orders_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate early flip alerts chronologically against later Kalshi quote crossings (research only)"
    )
    parser.add_argument("predictions", type=Path, help="Existing 1Hz predictions.jsonl file")
    parser.add_argument("--horizon-seconds", type=int, default=15)
    args = parser.parse_args()
    result = evaluate_prediction_stream(
        args.predictions, horizon_seconds=args.horizon_seconds,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
