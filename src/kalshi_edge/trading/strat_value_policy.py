"""Strat V2: conservative, MODEL-DEPENDENT settlement-value paper entry gate.

No network, no authenticated orders, no hypothetical maker fills.
Positive *estimated* value does NOT establish realized edge or calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .strat_policy import UpQuote
from .strat_paper import standard_taker_fee_estimate

D = Decimal
NS = 1_000_000_000
LOCK_SECONDS = 480
MAX_LOCK_LATENCY_NS = 2 * NS
MAX_ENTRY_AGE_NS = 30 * NS
MAX_RECEIPT_QUOTE_AGE_NS = 3 * NS
MIN_REMAINING_NS = 120 * NS
MIN_ORIGINAL_PROB = D("0.78")
SHRINK = D("0.75")  # heuristic, NOT an empirically calibrated credible bound
BUFFER = D("0.05")
FEE_UNCERTAINTY_MULTIPLIER = D("1.5")
SLIPPAGE = D("0.01")
MAX_SPREAD = D("0.03")
MAX_EFFECTIVE_BUY_PRICE = D("0.90")


@dataclass(frozen=True)
class ValueDecision:
    action: str
    reason: str
    side: str | None = None
    model_probability: D | None = None
    guarded_probability: D | None = None
    ask: D | None = None
    effective_buy_price: D | None = None
    fee_reserve_per_share: D | None = None
    estimated_edge_per_share: D | None = None
    expected_value_status: str = "MODEL_DEPENDENT_NOT_PROVEN"

    def as_dict(self) -> dict:
        return {
            name: str(val) if isinstance(val, D) else val
            for name, val in vars(self).items()
        }


def _num(value: Any) -> D | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (float, int, D, str)):
        return None
    try:
        x = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return x if x.is_finite() else None


def _reject(reason: str, side: str | None = None, **extras) -> ValueDecision:
    return ValueDecision("SKIP", reason, side=side, **extras)


def evaluate_value_entry(
    forecast: Mapping[str, Any] | None, quote: UpQuote,
    *, now_ns: int, close_ns: int,
) -> ValueDecision:
    """Only a properly sealed, linked 480s model forecast can justify buying.

    The haircut and buffer are *heuristic safeguards*; this routine must not be
    called an out-of-sample calibrated price or trading-profit guarantee.
    """
    if (type(now_ns) is not int or type(close_ns) is not int
            or not 0 < now_ns < close_ns):
        return _reject("INVALID_MARKET_CLOCK")
    if not isinstance(forecast, Mapping):
        return _reject("MISSING_FROZEN_FORECAST")
    ticker = forecast.get("market_ticker")
    if (not isinstance(ticker, str) or
            not ticker.startswith("KXBTC15M-") or
            not ticker.replace("-", "").isalnum()):
        return _reject("FORECAST_MARKET_MISMATCH")
    if forecast.get("forecast") not in ("ABOVE", "BELOW"):
        return _reject("INVALID_FROZEN_FORECAST")
    side = "UP" if forecast["forecast"] == "ABOVE" else "DOWN"
    if forecast.get("checkpoint_seconds") != LOCK_SECONDS:
        return _reject("NOT_480_SECOND_FROZEN_CHECKPOINT",side)
    scheduled=forecast.get("scheduled_ts_ns")
    prediction_ts=forecast.get("prediction_ts_ns")
    if (type(scheduled) is not int or type(prediction_ts) is not int or
            abs(scheduled + LOCK_SECONDS*NS - close_ns) > 2_000_000 or
            not scheduled <= prediction_ts <= scheduled+MAX_LOCK_LATENCY_NS):
        return _reject("CHECKPOINT_TIMESTAMP_MISMATCH",side)
    if now_ns - prediction_ts > MAX_ENTRY_AGE_NS:
        return _reject("ENTRY_WINDOW_EXPIRED",side)
    if now_ns < prediction_ts or close_ns-now_ns < MIN_REMAINING_NS:
        return _reject("ENTRY_OUTSIDE_VERIFIED_WINDOW",side)
    model_hash=forecast.get("model_hash")
    if (not isinstance(model_hash,str) or len(model_hash)!=64 or
            any(ch not in "0123456789abcdef" for ch in model_hash.lower()) or
            not isinstance(forecast.get("model_version"),str) or
            not forecast["model_version"].strip()):
        return _reject("UNVERIFIED_MODEL_PROVENANCE",side)
    if (forecast.get("coin_flip_assessment") != "LOW" or
            (_num(forecast.get("coin_flip_risk_z")) or D("-1")) <= D("0.20")):
        return _reject("NEAR_STRIKE_RISK",side)
    yes=_num(forecast.get("p_yes"))
    no=_num(forecast.get("p_no"))
    conf=_num(forecast.get("confidence"))
    directional=_num(forecast.get("directional_probability"))
    if (yes is None or no is None or
            not D("0") <= yes <= 1 or not D("0") <= no <= 1 or
            abs(yes + no - D("1")) > D("0.002")):
        return _reject("INCONSISTENT_MODEL_PROBABILITIES",side)
    model_p = yes if side=="UP" else no
    opposing_p = no if side=="UP" else yes
    if model_p <= opposing_p:
        return _reject("MODEL_DIRECTION_CONFLICT",side)
    if (conf is None or directional is None or
            not D("0.69") <= conf <= 1 or
            abs(directional-model_p)>D("0.01") or
            model_p < MIN_ORIGINAL_PROB):
        return _reject("MODEL_CONFIDENCE_OR_PROBABILITY_GATE",side)
    guarded = D("0.5") + SHRINK*(model_p-D("0.5"))
    common = {"model_probability":model_p, "guarded_probability":guarded}
    if not isinstance(quote, UpQuote) or quote.ticker != ticker:
        return _reject("QUOTE_MARKET_MISMATCH",side,**common)
    if (quote.bid_depth <= 0 or quote.ask_depth <= 0):
        return _reject("NO_EXECUTABLE_DEPTH",side,**common)
    if not quote.valid():
        return _reject("INVALID_EXECUTABLE_QUOTE",side,**common)
    if not 0 <= now_ns-quote.observed_ns <= MAX_RECEIPT_QUOTE_AGE_NS:
        return _reject("STALE_QUOTE",side,**common)
    if quote.observed_ns < prediction_ts or quote.observed_ns > close_ns:
        return _reject("QUOTE_OUTSIDE_FROZEN_WINDOW",side,**common)
    if quote.ask-quote.bid > MAX_SPREAD:
        return _reject("SPREAD_TOO_WIDE",side,**common)
    price=quote.ask+SLIPPAGE
    if price > MAX_EFFECTIVE_BUY_PRICE:
        return _reject("INSUFFICIENT_PRICE_HEADROOM",side,**common,
                       ask=quote.ask,effective_buy_price=price)
    reserve=standard_taker_fee_estimate(price,D("1"))*FEE_UNCERTAINTY_MULTIPLIER
    edge=guarded-price-reserve-BUFFER
    if edge <= 0:
        return _reject("INSUFFICIENT_NET_VALUE_EDGE",side,**common,
                       ask=quote.ask,effective_buy_price=price,
                       fee_reserve_per_share=reserve,
                       estimated_edge_per_share=edge)
    return ValueDecision("BUY","POSITIVE_GUARDED_SETTLEMENT_VALUE",side=side,
                         **common,ask=quote.ask,effective_buy_price=price,
                         fee_reserve_per_share=reserve,
                         estimated_edge_per_share=edge)
