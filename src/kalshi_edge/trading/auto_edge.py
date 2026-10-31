"""Conservative, fee-inclusive pretrade value gate for AutoTrade BUYs.

KalshiEdge's live model reports confidence = 2 * abs(p_yes - 0.5).
Thus the model-implied probability of its selected side is
(1 + confidence) / 2, NOT the displayed confidence itself.

This is a *model-based filter*, not evidence of calibrated probabilities or
future profitability. An additional three-percentage-point haircut and
a minimum expected-value margin address modest model/fee uncertainty.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


WIN_PROBABILITY_HAIRCUT = Decimal("0.03")
MIN_NET_EXPECTED_DOLLARS = Decimal("0.005")
MIN_EDGE_ON_SPEND = Decimal("0.02")
QUOTE_ROUNDING_TOLERANCE = Decimal("0.0001")


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing monetary input")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("Invalid monetary input") from exc
    if not result.is_finite():
        raise ValueError("Nonfinite monetary input")
    return result


def assess_auto_buy_edge(preview: dict, confidence: float) -> dict[str, object]:
    """Return fail-closed model-based expected net value for a proposed BUY.

    This is deliberately called after a fresh exchange quote, before local
    journal mutation or any signed POST. It never places an order.
    """
    invalid = {"approved": False, "reason": "INVALID_QUOTE"}
    if not isinstance(preview, dict):
        return invalid
    try:
        if isinstance(confidence, bool):
            return invalid
        conf = _number(confidence)
        if not Decimal("0") <= conf <= Decimal("1"):
            return invalid
        price = _number(preview.get("unit_price"))
        count = _number(preview.get("count"))
        fee = _number(preview.get("fee_estimate"))
        debit = _number(preview.get("amount_estimate"))
        if not (Decimal("0") < price < Decimal("1")
                and count > 0 and fee >= 0 and debit > 0):
            return invalid
        expected_debit = price * count + fee
        # The preview amount is rounded to 4 decimal places; allow at most
        # one last-digit rounding unit, not an arbitrary understating of fees.
        if debit + QUOTE_ROUNDING_TOLERANCE < expected_debit:
            return invalid
        conservative_debit = max(debit, expected_debit)
    except ValueError:
        return invalid

    estimated_win_probability = (Decimal("1") + conf) / Decimal("2")
    conservative_win_probability = max(
        Decimal("0"), estimated_win_probability - WIN_PROBABILITY_HAIRCUT
    )
    expected_net = conservative_win_probability * count - conservative_debit
    minimum = max(MIN_NET_EXPECTED_DOLLARS,
                  MIN_EDGE_ON_SPEND * conservative_debit)
    approved = expected_net >= minimum
    reason = ("APPROVED" if approved else
              "NON_POSITIVE_EDGE" if expected_net <= 0 else "INSUFFICIENT_EDGE")
    return {
        "approved": approved,
        "reason": reason,
        "model_win_probability": format(estimated_win_probability, ".6f"),
        "conservative_win_probability": format(conservative_win_probability, ".6f"),
        "expected_net_dollars": format(expected_net, ".6f"),
        "minimum_required_dollars": format(minimum, ".6f"),
        "fee_inclusive_debit_dollars": format(conservative_debit, ".6f"),
    }
