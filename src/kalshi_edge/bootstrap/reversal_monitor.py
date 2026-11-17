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
FLIP_LIFETIME_NS = 20 * NS
DIRECTION_HYSTERESIS = 0.06


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


def _recent_flip(series: list[dict[str, Any]], now_ns: int) -> dict[str, Any] | None:
    """Require two sequential, decisive samples on both sides of a flip.

    An exchange quote move in the same direction is also required. This
    prevents model-only midline noise from becoming a flip alert.
    """
    confirmed_side = None
    confirmed_mid = None
    candidate = None
    count = 0
    last_ts = None
    detected = None
    for item in series:
        side = _direction(item["p_yes"])
        ts = item["prediction_ts_ns"]
        if side is None:
            candidate, count = None, 0
            last_ts = ts
            continue
        if side == candidate and last_ts is not None and ts - last_ts <= MAX_GAP_NS:
            count += 1
        else:
            candidate, count = side, 1
        last_ts = ts
        if count != 2 or side == confirmed_side:
            continue
        mid = _quote_mid(item)
        if confirmed_side is not None and confirmed_mid is not None and mid is not None:
            delta = mid - confirmed_mid
            # 1.5 cents in Kalshi YES contract price, not 1.5% BTC.
            price_confirmed = delta >= .015 if side == "UP" else delta <= -.015
            if price_confirmed:
                detected = {
                    "direction": confirmed_side + "_TO_" + side,
                    "detected_ts_ns": ts,
                }
        confirmed_side, confirmed_mid = side, mid

    if detected is None or now_ns - detected["detected_ts_ns"] > FLIP_LIFETIME_NS:
        return None
    return {
        **detected,
        "age_seconds": max(0, (now_ns - detected["detected_ts_ns"]) // NS),
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

    alert = _recent_flip(contiguous, now_ns)
    if alert or adverse_p >= .07 or adverse_book >= .05 or near_neutral < .06:
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
