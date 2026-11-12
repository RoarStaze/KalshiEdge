"""Classify actual fill validation failures without leaking exchange payloads.

All responses are simulated. This module never loads keys or places orders.
"""
from copy import deepcopy

import pytest

from kalshi_edge.trading.autotrade_pnl import enrich_history

TICKER = "KXBTC15M-FILL-VALIDATION-TEST"


def journal():
    return [{
        "ticker": TICKER, "status": "FILLED", "outcome": "UP",
        "stages": [{
            "stage": "FINAL_BUY", "outcome": "UP", "status": "FILLED",
            "order_id": "test-order", "fill_count": "0.10",
        }],
    }]


def normal_fill():
    return {
        "ticker": TICKER, "market_ticker": TICKER,
        "order_id": "test-order", "fill_id": "test-fill",
        "outcome_side": "yes", "book_side": "bid",
        "side": "yes", "action": "buy",
        "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
        "count_fp": "0.10", "fee_cost": "0.001000",
    }


class SimulatedExchange:
    def __init__(self, fills):
        self.fills = fills

    def get(self, path, *, params=None):
        if path == "/portfolio/settlements":
            return {"settlements": [{
                "ticker": TICKER, "market_result": "yes",
                "yes_count_fp": "0.10", "no_count_fp": "0.00",
            }], "cursor": ""}
        if path == "/portfolio/fills":
            return {"fills": deepcopy(self.fills), "cursor": ""}
        raise AssertionError("Audit requested an unexpected API route")


@pytest.mark.parametrize("change,expected", [
    ({"yes_price_dollars": "0.5300"}, "FILL_PRICE_INCONSISTENT"),
    ({"yes_price_dollars": None, "no_price_dollars": None}, "FILL_PRICE_INVALID"),
    ({"fee_cost": None}, "FILL_FEE_INVALID"),
    ({"fee_cost": "-0.003000"}, "FILL_FEE_INVALID"),
    ({"count_fp": "NaN"}, "FILL_QUANTITY_INVALID"),
    ({"outcome_side": "yes", "book_side": "ask"}, "FILL_DIRECTION_CONFLICT"),
    ({"market_ticker": TICKER, "ticker": "KXBTC15M-OTHER"}, "FILL_MARKET_MISMATCH"),
    ({"outcome_side": None, "book_side": None,
      "side": None, "action": None}, "FILL_IDENTITY_MISSING"),
])
def test_invalid_authenticated_fill_has_specific_safe_reason(change, expected):
    fill = normal_fill()
    fill.update(change)
    row = enrich_history(
        journal(), SimulatedExchange([fill]),
        require_settlement_counts=True,
    )[0]
    assert row["pnl_reason"] == expected
    assert row["pnl_status"] == "UNAVAILABLE"
    assert row["realized_pnl_dollars"] is None


def test_duplicate_fill_id_is_not_classified_as_generic_invalid():
    item = normal_fill()
    row = enrich_history(
        journal(), SimulatedExchange([item, deepcopy(item)]),
        require_settlement_counts=True,
    )[0]
    assert row["pnl_reason"] == "FILL_ID_DUPLICATE"
    assert row["realized_pnl_dollars"] is None


def test_valid_fill_still_reconciles_actual_fee_inclusive_profit():
    row = enrich_history(
        journal(), SimulatedExchange([normal_fill()]),
        require_settlement_counts=True,
    )[0]
    assert row["pnl_reason"] == "VERIFIED"
    assert row["realized_pnl_dollars"] == "0.0390"
