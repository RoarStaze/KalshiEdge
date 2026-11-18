"""Causal read-only BTC15M short-horizon instability and pre-flip diagnostics.

This monitors *changes in the deployed end-of-market probability estimate*
and live Kalshi/BRTI/crypto market evidence. It is not a separately fitted
short-horizon predictor or a calibrated probability of a profitable scalp.
Every threshold is provisional and must be validated chronologically on
prospective, previously unseen market periods before using it in execution.
No order permissions or auto-trade decisions are made here.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

NS = 1_000_000_000
WINDOW_NS = 90 * NS
MAX_AGE_NS = 5 * NS
MAX_GAP_NS = 4 * NS
DIRECTION_HYSTERESIS = .06
PRE_FLIP_WINDOW_SECONDS = 15
PRE_FLIP_MIN_PROBABILITY_DRIFT = .055
PRE_FLIP_MIN_QUOTE_DRIFT = .02
HORIZONS = (5, 15, 30)


def _finite(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value)


def _unavailable() -> dict[str, Any]:
    return {
        "level": "UNAVAILABLE",
        "reason": "INSUFFICIENT_OR_STALE_LIVE_EVIDENCE",
        "flip_alert": None,
        "risk_score": None,
        "calibrated_probability": None,
        "risk_drivers": [],
        "signal_quality": "UNAVAILABLE",
        "evidence_sources": [],
        "source": "LIVE_MODEL_AND_KALSHI_QUOTE",
        "live_orders_enabled": False,
    }


def _quote_mid(item: dict[str, Any]) -> float | None:
    bid, ask = item.get("kalshi_yes_bid"), item.get("kalshi_yes_ask")
    if not all(_finite(v) for v in (bid, ask)):
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
        and _finite(p)
        and 0 <= p <= 1
        and _quote_mid(item) is not None
    )


def _direction(p: float) -> str | None:
    if p >= .5 + DIRECTION_HYSTERESIS:
        return "UP"
    if p <= .5 - DIRECTION_HYSTERESIS:
        return "DOWN"
    return None


def _anchor(series: Sequence[dict[str, Any]], seconds: int) -> dict[str, Any] | None:
    newest = series[-1]["prediction_ts_ns"]
    return next(
        (item for item in reversed(series[:-1])
         if newest - item["prediction_ts_ns"] >= seconds * NS), None
    )


def _fresh_value(item: dict[str, Any], field: str, ts_field: str) -> float | None:
    val, source = item.get(field), item.get(ts_field)
    ts = item.get("prediction_ts_ns")
    if not _finite(val) or type(ts) is not int or type(source) is not int:
        return None
    if source > ts or ts - source > MAX_AGE_NS:
        return None
    return float(val)


def _brti(item: dict[str, Any]) -> float | None:
    result = _fresh_value(item, "brti_value", "brti_source_ts_ns")
    return result if result is not None and result > 0 else None


def _binance(item: dict[str, Any]) -> float | None:
    result = _fresh_value(item, "btc_reference", "binance_source_ts_ns")
    return result if result is not None and result > 0 else None


def _strike(item: dict[str, Any]) -> float | None:
    strike = item.get("strike")
    return float(strike) if _finite(strike) and strike > 0 else None


def _depth_imbalance(item: dict[str, Any]) -> float | None:
    bid, ask = item.get("kalshi_yes_bid_depth"), item.get("kalshi_yes_ask_depth")
    if not all(_finite(v) and v >= 0 for v in (bid, ask)) or bid + ask <= 0:
        return None
    return (bid - ask) / (bid + ask)


def _taker_imbalance(item: dict[str, Any]) -> float | None:
    volume = item.get("kalshi_taker_total_volume_10s")
    signed = item.get("kalshi_taker_signed_volume_10s")
    if not (_finite(volume) and _finite(signed) and volume >= 0
            and abs(signed) <= volume + 1e-8):
        return None
    return signed / volume if volume > 0 else 0.0


def _pre_flip_warning(
    series: list[dict[str, Any]], baseline: dict[str, Any], now_ns: int,
) -> dict[str, Any] | None:
    """Pre-crossing pressure warning; future outcomes are never consulted."""
    latest = series[-1]
    p = latest["p_yes"]
    if p == .5:
        return None
    current = "UP" if p > .5 else "DOWN"
    target = "DOWN" if current == "UP" else "UP"
    toward = -1 if current == "UP" else 1
    if _direction(baseline["p_yes"]) != current:
        return None
    distance_to_cross = abs(p - .5)
    if not .005 <= distance_to_cross <= .17:
        return None

    mid = _quote_mid(latest)
    baseline_mid = _quote_mid(baseline)
    # The quote must not have already crossed the binary 50c boundary.
    if mid is None or baseline_mid is None or (mid - .5) * -toward <= 0:
        return None
    delta_p = toward * (p - baseline["p_yes"])
    delta_book = toward * (mid - baseline_mid)
    if delta_p < PRE_FLIP_MIN_PROBABILITY_DRIFT or delta_book < PRE_FLIP_MIN_QUOTE_DRIFT:
        return None
    elapsed_s = (latest["prediction_ts_ns"] - baseline["prediction_ts_ns"]) / NS
    if elapsed_s <= 0 or distance_to_cross / (delta_p / elapsed_s) > PRE_FLIP_WINDOW_SECONDS:
        return None

    recent = _anchor(series, 2)
    if recent is None:
        return None
    recent_mid = _quote_mid(recent)
    if (recent_mid is None
            or toward * (p - recent["p_yes"]) < .008
            or toward * (mid - recent_mid) < .005):
        return None

    support = ["model", "kalshi_quote"]
    extra_sources = 0
    extra_support = 0
    strike = _strike(latest)
    b0, b1 = _brti(baseline), _brti(latest)
    if strike is not None and b0 is not None and b1 is not None:
        extra_sources += 1
        # BRTI is the relevant settlement reference, not a hypothetical trade.
        # If it has ALREADY moved through the strike, we are too late to
        # classify a pre-BRTI flip as an early warning.
        if (b1 - strike) * -toward <= 0:
            return None
        if toward * (b1 - b0) / strike * 10_000 >= .45:
            support.append("brti")
            extra_support += 1
    depth = _depth_imbalance(latest)
    if depth is not None:
        extra_sources += 1
        if toward * depth >= .22:
            support.append("queue_imbalance")
            extra_support += 1
    taker = _taker_imbalance(latest)
    total = latest.get("kalshi_taker_total_volume_10s")
    if taker is not None and _finite(total) and total >= 1.0:
        extra_sources += 1
        if toward * taker >= .25:
            support.append("taker_flow")
            extra_support += 1
    x0, x1 = _binance(baseline), _binance(latest)
    if strike is not None and x0 is not None and x1 is not None:
        extra_sources += 1
        if toward * (x1 - x0) / strike * 10_000 >= .45:
            support.append("binance")
            extra_support += 1
    # When additional independent sources really are available, demand
    # corroboration; legacy data without them can still use model+quote.
    if extra_sources >= 2 and extra_support == 0:
        return None
    return {
        "kind": "PRE_FLIP_WARNING",
        "direction": target,
        "current_direction": current,
        "detected_ts_ns": latest["prediction_ts_ns"],
        "age_seconds": max(0, (now_ns - latest["prediction_ts_ns"]) // NS),
        "supporting_signals": support,
        "horizon_seconds": PRE_FLIP_WINDOW_SECONDS,
        "calibrated_probability": None,
    }


def assess_reversal_risk(
    samples: Sequence[dict[str, Any]], *, now_ns: int, market_ticker: str,
) -> dict[str, Any]:
    """Composite heuristic risk and pre-flip warning from causal live evidence.

    All window features come from the same current market, with no record
    newer than now_ns and no reconnection/gap interpolation.
    """
    missing = _unavailable()
    if type(now_ns) is not int or now_ns <= 0 or not market_ticker or not samples:
        return missing
    matching = [s for s in samples if isinstance(s, dict) and s.get("market_ticker") == market_ticker]
    if not matching:
        return missing
    matching.sort(key=lambda s: s.get("prediction_ts_ns") if type(s.get("prediction_ts_ns")) is int else -1)
    latest = matching[-1]
    if not _valid(latest, market_ticker):
        return missing
    age_ns = now_ns - latest["prediction_ts_ns"]
    if not 0 <= age_ns <= MAX_AGE_NS:
        return missing

    contiguous = []
    next_ts = None
    epoch = latest.get("market_stream_epoch")
    for item in reversed(matching):
        if not _valid(item, market_ticker):
            break
        # A reconnect can occur within the predictor's 1Hz interval:
        # never join a new order-book stream to earlier observations.
        if type(epoch) is int and item.get("market_stream_epoch") != epoch:
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
        return missing
    baseline = _anchor(contiguous, 7)
    if baseline is None:
        return missing

    p, old_p = latest["p_yes"], baseline["p_yes"]
    mid, old_mid = _quote_mid(latest), _quote_mid(baseline)
    sign = 1 if p >= .5 else -1
    adverse_p = max(0.0, -sign * (p - old_p))
    adverse_book = max(0.0, -sign * (mid - old_mid))
    drivers: list[str] = []
    score = 0.0

    def add(reason: str, points: float) -> None:
        nonlocal score
        if points > 0:
            drivers.append(reason)
            score += points

    changes: dict[str, dict[str, float]] = {}
    model_trend = quote_trend = 0.0
    for horizon in HORIZONS:
        older = _anchor(contiguous, horizon)
        if older is None:
            continue
        p_delta = p - older["p_yes"]
        quote_delta = mid - _quote_mid(older)
        changes[str(horizon)] = {
            "model": round(p_delta, 6), "kalshi_quote": round(quote_delta, 6),
        }
        model_trend = max(model_trend, min(22.0, max(0., -sign*p_delta) / (.025 + .002*horizon) * 14.))
        quote_trend = max(quote_trend, min(16.0, max(0., -sign*quote_delta) / (.018 + .001*horizon) * 11.))
    if model_trend >= 4:
        add("MODEL_ADVERSE_TREND", model_trend)
    if quote_trend >= 4:
        add("KALSHI_QUOTE_ADVERSE_TREND", quote_trend)

    distance = abs(p - .5)
    if distance < .16:
        add("MODEL_NEAR_50", min(17.0, (0.16-distance)/.16*17.0))
    spread = latest["kalshi_yes_ask"] - latest["kalshi_yes_bid"]
    if spread >= .08:
        add("WIDE_SPREAD", 30.)
    elif spread >= .04:
        add("WIDENING_SPREAD", 8.)

    sources = ["model", "kalshi_quote"]
    b = _brti(latest)
    strike = _strike(latest)
    if b is not None and strike is not None:
        sources.append("brti")
        bps = abs(b - strike) / strike * 10000
        if bps <= 3.:
            add("NEAR_STRIKE", min(18.0, 18.0 * (3.5-bps)/3.5))
        ratio = latest.get("brti_shadow_risk_ratio")
        if _finite(ratio) and ratio < .6:
            add("BRTI_VOLATILITY_PRESSURE", 7.)
    else:
        bps = None
    if _binance(latest) is not None:
        sources.append("binance")

    disagreement = latest.get("component_disagreement")
    if _finite(disagreement) and 0 <= disagreement <= 1:
        sources.append("model_components")
        if disagreement >= .25:
            add("MODEL_DISAGREEMENT", min(18.0, 12.0+max(0, disagreement-.25)*18.0))

    queue = _depth_imbalance(latest)
    taker = _taker_imbalance(latest)
    if queue is not None and taker is not None:
        sources.append("microstructure")
    if queue is not None and -sign*queue > .25:
        add("ADVERSE_QUEUE_IMBALANCE", min(12., -sign*queue*13.))
    if taker is not None and -sign*taker > .30 and latest.get("kalshi_taker_total_volume_10s", 0) >= 1.:
        add("ADVERSE_TAKER_FLOW", min(12., -sign*taker*12.))

    alert = _pre_flip_warning(contiguous, baseline, now_ns)
    if alert is not None:
        add("EARLY_FLIP_PRESSURE", 10.)
    # 7s legacy circuit-breaker is deliberately retained; it catches abrupt
    # adverse moves even before 15/30s windows accumulate evidence.
    if (alert is not None or spread >= .1 or adverse_p >= .05
            or adverse_book >= .04 or score >= 44):
        level = "HIGH"
    elif score >= 18 or adverse_p >= .025 or adverse_book >= .02:
        level = "MEDIUM"
    else:
        level = "LOW"

    full_sources = {"model","kalshi_quote","brti","binance","microstructure","model_components"}
    quality = "FULL" if full_sources.issubset(set(sources)) else "PARTIAL"
    return {
        "level": level,
        "reason": "HEURISTIC_MULTISIGNAL_INSTABILITY_V2",
        "risk_score": min(100, round(score)),
        "calibrated_probability": None,
        "signal_quality": quality,
        "evidence_sources": sources,
        "risk_drivers": drivers,
        "window_deltas": changes,
        "spread_dollars": round(spread, 6),
        "strike_distance_bps": None if bps is None else round(bps, 3),
        "quote_queue_imbalance": None if queue is None else round(queue, 4),
        "taker_imbalance": None if taker is None else round(taker, 4),
        "flip_alert": alert,
        "source": "LIVE_MODEL_AND_KALSHI_QUOTE",
        "live_orders_enabled": False,
        "observed_ts_ns": latest["prediction_ts_ns"],
        "observation_count": len(contiguous),
        "adverse_probability_change": round(adverse_p, 6),
        "adverse_book_change_dollars": round(adverse_book, 6),
    }
