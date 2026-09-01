from __future__ import annotations

from datetime import datetime
from typing import Any

from .types import MarketLabel


class LabelNormalizationError(ValueError):
    pass


def _iso_to_ns(value: Any, *, field: str, required: bool = True) -> int | None:
    if value in (None, ""):
        if required:
            raise LabelNormalizationError(f"missing {field}")
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LabelNormalizationError(f"invalid {field}") from exc
    if parsed.tzinfo is None:
        raise LabelNormalizationError(f"{field} must be timezone-aware")
    return int(parsed.timestamp() * 1_000_000_000)


def normalize_market_label(payload: dict[str, Any]) -> MarketLabel:
    market_value = payload.get("market", payload)
    if not isinstance(market_value, dict):
        raise LabelNormalizationError("market payload must be an object")
    market = market_value

    ticker = market.get("ticker")
    result = str(market.get("result") or "").lower()
    settlement_raw = market.get("settlement_value_dollars")
    strike_raw = market.get("floor_strike")
    strike_type = str(market.get("strike_type") or "").lower()

    if not ticker:
        raise LabelNormalizationError("missing ticker")
    if result not in {"yes", "no"}:
        raise LabelNormalizationError("market result is unresolved or unsupported")
    if market.get("is_provisional") is True:
        raise LabelNormalizationError("provisional market cannot be a training label")
    if settlement_raw in (None, ""):
        raise LabelNormalizationError("missing settlement value")
    if strike_raw is None:
        raise LabelNormalizationError("missing floor strike")
    if strike_type != "greater":
        raise LabelNormalizationError(f"unsupported or ambiguous strike_type: {strike_type or '<missing>'}")

    try:
        settlement_value = float(settlement_raw)
        strike = float(strike_raw)
    except (TypeError, ValueError) as exc:
        raise LabelNormalizationError("non-numeric strike or settlement value") from exc

    return MarketLabel(
        ticker=str(ticker),
        event_ticker=str(market.get("event_ticker")) if market.get("event_ticker") else None,
        strike=strike,
        strike_type=strike_type,
        yes_is_above=True,
        result=result,
        settlement_value=settlement_value,
        open_ts_ns=_iso_to_ns(market.get("open_time"), field="open_time"),
        close_ts_ns=_iso_to_ns(market.get("close_time"), field="close_time"),
        settlement_ts_ns=_iso_to_ns(market.get("settlement_ts"), field="settlement_ts", required=False),
        rules_primary=str(market.get("rules_primary")) if market.get("rules_primary") else None,
        rules_secondary=str(market.get("rules_secondary")) if market.get("rules_secondary") else None,
    )
