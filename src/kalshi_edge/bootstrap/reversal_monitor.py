"""Causal, read-only intramarket reversal and potential flip diagnostics.

The existing 480s model estimates terminal settlement direction, not the
probability of profitable 5-30s trades. These deliberately transparent
thresholds are monitoring heuristics, not calibrated reversal probabilities.
No order permission or automatic trade instruction is produced.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

NS = 1_000_000_000
WINDOW_NS = 45 * NS
MAX_AGE_NS = 5 * NS
MAX_GAP_NS = 4 * NS
DIRECTION_HYSTERESIS = 0.06
PRE_FLIP_WINDOW_SECONDS = 15
PRE_FLIP_MIN_PROBABILITY_DRIFT = 0.055
PRE_FLIP_MIN_QUOTE_DRIFT = 0.02


def _unavailable() -> dict[str, Any]:
    return {
        "level": "UNAVAILABLE",
        "reason": "INSUFFICIENT_OR_STALE_LIVE_EVIDENCE",
        "flip_alert": None,
        "source": "LIVE_MODEL_AND_KALSHI_QUOTE",
        "live_orders_enabled": False,
    }


def _quote_mid(item: dict[str, Any]) -> float | None:
    bid, ask = item.get("kalshi_yes_bid"), item.get("kalshi_yes_ask")
    if not all(type(v) in (float, int) and math.isfinite(v) for v in (bid, ask)):
        return None
    if not (0 < bid <= ask < 1):
        return None
    return (bid + ask) / 2


def _valid(item: Any, ticker: str) -> bool:
    if not isinstance(item, dict) or item.get("market_ticker") != ticker:
        return False
    p = item.get("p_yes")
    return (
        item.get("status") == "OK"
        and isinstance(item.get("feed_quality"), dict)
        and item["feed_quality"].get("healthy") is True
        and type(item.get("prediction_ts_ns")) is int
        and type(p) in (float, int)
        and math.isfinite(p)
        and 0 <= p <= 1
        and _quote_mid(item) is not None
    )


def _direction(p: float) -> str | None:
    if p >= .5 + DIRECTION_HYSTERESIS:
        return "UP"
    if p <= .5 - DIRECTION_HYSTERESIS:
        return "DOWN"
    return None


def _pre_flip_warning(
    series: list[dict[str, Any]], baseline: dict[str, Any], now_ns: int,
) -> dict[str, Any] | None:
    """Causal *early* model-side warning, never an already-confirmed flip.

    The model predicts end-of-round settlement, NOT an independently trained
    short-horizon reversal probability. These provisional 15-second lookahead
    thresholds are monitoring heuristics, requiring a model move AND a Kalshi
    YES quote move. Future outcomes cannot be guaranteed or inferred here.
    """
    latest = series[-1]
    p = latest["p_yes"]
    if p == .5:
        return None
    current = "UP" if p > .5 else "DOWN"
    target = "DOWN" if current == "UP" else "UP"
    toward = -1 if current == "UP" else 1
    # Old side must still be decisive; do not label an already completed
    # p_yes 50% crossing as a prediction or recycle earlier confirmations.
    if _direction(baseline["p_yes"]) != current:
        return None
    distance_to_cross = abs(p - .5)
    if not .005 <= distance_to_cross <= .17:
        return None

    mid = _quote_mid(latest)
    baseline_mid = _quote_mid(baseline)
    # The *exchange quote*, too, must remain on the old side. Otherwise
    # the alert merely describes a price flip that has already occurred.
    if mid is None or baseline_mid is None or (mid - .5) * -toward <= 0:
        return None
    delta_p = toward * (p - baseline["p_yes"])
    delta_book = toward * (mid - baseline_mid)
    if delta_p < PRE_FLIP_MIN_PROBABILITY_DRIFT or delta_book < PRE_FLIP_MIN_QUOTE_DRIFT:
        return None
    elapsed_s = (latest["prediction_ts_ns"] - baseline["prediction_ts_ns"]) / NS
    if elapsed_s <= 0 or distance_to_cross / (delta_p / elapsed_s) > PRE_FLIP_WINDOW_SECONDS:
        return None

    # The newest observations must support the trend. A rebound should
    # immediately clear the warning, not leave an old red/green alert lit.
    recent = next(
        (item for item in reversed(series[:-1])
         if latest["prediction_ts_ns"] - item["prediction_ts_ns"] >= 2 * NS),
        None,
    )
    if recent is None:
        return None
    recent_mid = _quote_mid(recent)
    if (recent_mid is None
            or toward * (p - recent["p_yes"]) < .008
            or toward * (mid - recent_mid) < .005):
        return None
    return {
        "kind": "PRE_FLIP_WARNING",
        "direction": target,
        "current_direction": current,
        "detected_ts_ns": latest["prediction_ts_ns"],
        "age_seconds": max(0, (now_ns - latest["prediction_ts_ns"]) // NS),
    }


def assess_reversal_risk(
    samples: Sequence[dict[str, Any]], *, now_ns: int, market_ticker: str,
) -> dict[str, Any]:
    """Assess short-run adverse drift against CURRENT model-preferred side.

    Fail closed on invalid, stale, interrupted or insufficient observations.
    This describes *signal instability*, not executable P/L or a calibrated
    future reversal probability.
    """
    result = _unavailable()
    if type(now_ns) is not int or now_ns <= 0 or not market_ticker or not samples:
        return result
    matching = [s for s in samples if isinstance(s, dict) and s.get("market_ticker") == market_ticker]
    if not matching:
        return result
    matching.sort(key=lambda s: s.get("prediction_ts_ns") if type(s.get("prediction_ts_ns")) is int else -1)
    latest = matching[-1]
    if not _valid(latest, market_ticker):
        return result
    age_ns = now_ns - latest["prediction_ts_ns"]
    if not 0 <= age_ns <= MAX_AGE_NS:
        return result

    contiguous = []
    next_ts = None
    for item in reversed(matching):
        if not _valid(item, market_ticker):
            break
        ts = item["prediction_ts_ns"]
        if ts > now_ns or now_ns - ts > WINDOW_NS:
            break
        if next_ts is not None and not 0 < next_ts - ts <= MAX_GAP_NS:
            break
        contiguous.append(item)
        next_ts = ts
    contiguous.reverse()
    if len(contiguous) < 8 or latest["prediction_ts_ns"] - contiguous[0]["prediction_ts_ns"] < 7 * NS:
        return result

    # The first observation at least seven seconds older than latest defines
    # a reproducible adverse-drift horizon. Never use a future observation.
    baseline = next(
        (s for s in reversed(contiguous) if latest["prediction_ts_ns"] - s["prediction_ts_ns"] >= 7 * NS),
        None,
    )
    if baseline is None:
        return result
    p, old_p = latest["p_yes"], baseline["p_yes"]
    mid, old_mid = _quote_mid(latest), _quote_mid(baseline)
    side_sign = 1 if p >= .5 else -1
    adverse_p = max(0.0, -side_sign * (p - old_p))
    adverse_book = max(0.0, -side_sign * (mid - old_mid))
    near_neutral = abs(p - .5)

    alert = _pre_flip_warning(contiguous, baseline, now_ns)
    if alert or adverse_p >= .05 or adverse_book >= .04 or near_neutral < .06:
        level = "HIGH"
    elif adverse_p >= .025 or adverse_book >= .02 or near_neutral < .15:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "level": level,
        "reason": "HEURISTIC_MODEL_AND_BOOK_INSTABILITY",
        "flip_alert": alert,
        "source": "LIVE_MODEL_AND_KALSHI_QUOTE",
        "live_orders_enabled": False,
        "observed_ts_ns": latest["prediction_ts_ns"],
        "observation_count": len(contiguous),
        "adverse_probability_change": round(adverse_p, 6),
        "adverse_book_change_dollars": round(adverse_book, 6),
    }
