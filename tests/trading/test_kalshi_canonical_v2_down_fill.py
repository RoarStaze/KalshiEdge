"""Canonical Kalshi V2 fill direction must match official outcome/book equivalence.

Source: https://docs.kalshi.com/getting_started/order_direction
yes == bid; no == ask. Legacy action/side is validated separately.
All exchange responses are synthetic and no orders or credentials are used.
"""
from __future__ import annotations

import pytest

from kalshi_edge.trading.fill_reconciliation import (
    _scheme_exposure,
    validated_fill_unit_price,
)
from kalshi_edge.trading.autotrade_pnl import enrich_history

TICKER = "KXBTC15M-26NOV130530-30"


@pytest.mark.parametrize("side,book,expected", [
    ("yes", "bid", 1),
    ("no", "ask", -1),
])
def test_canonical_v2_equivalent_exposures(side, book, expected):
    assert _scheme_exposure(side, book, flavor="v2") == expected


@pytest.mark.parametrize("side,book", [
    ("yes", "ask"),
    ("no", "bid"),
])
def test_canonical_v2_inconsistent_pair_is_rejected(side, book):
    with pytest.raises(ValueError, match="V2"):
        _scheme_exposure(side, book, flavor="v2")


@pytest.mark.parametrize("legacy_side,legacy_action", [
    ("no", "buy"),
    ("yes", "sell"),
])
def test_realistic_down_fill_can_validate_with_consistent_legacy_aliases(
    legacy_side, legacy_action,
):
    fill = {
        "ticker": TICKER, "order_id": "synthetic-order",
        "outcome_side": "no", "book_side": "ask",
        "side": legacy_side, "action": legacy_action,
        "yes_price_dollars": "0.2000", "no_price_dollars": "0.8000",
    }
    assert validated_fill_unit_price(
        fill, ticker=TICKER, action="BUY", outcome="DOWN",
    ) == 0.8


@pytest.mark.parametrize("legacy_side,legacy_action", [
    ("yes", "buy"),
    ("no", "sell"),
])
def test_down_fill_still_rejects_conflicting_legacy_aliases(
    legacy_side, legacy_action,
):
    fill = {
        "ticker": TICKER, "order_id": "synthetic-order",
        "outcome_side": "no", "book_side": "ask",
        "side": legacy_side, "action": legacy_action,
        "yes_price_dollars": "0.2000", "no_price_dollars": "0.8000",
    }
    with pytest.raises(ValueError, match="conflicts"):
        validated_fill_unit_price(
            fill, ticker=TICKER, action="BUY", outcome="DOWN",
        )


def test_strict_realized_down_profit_with_correct_no_ask_direction():
    entry = {
        "ticker": TICKER, "status": "FILLED", "outcome": "DOWN",
        "stages": [{
            "stage": "FINAL_BUY", "outcome": "DOWN",
            "status": "FILLED", "order_id": "synthetic-order",
            "fill_count": "0.11",
        }],
    }
    matched_fill = {
        "ticker": TICKER, "order_id": "synthetic-order",
        "fill_id": "synthetic-fill",
        "outcome_side": "no", "book_side": "ask",
        "side": "no", "action": "buy",
        "count_fp": "0.11",
        "yes_price_dollars": "0.2000", "no_price_dollars": "0.8000",
        "fee_cost": "0.002000",
    }

    class FakeAccount:
        def get(self, path, *, params=None):
            if path == "/portfolio/settlements":
                return {
                    "settlements": [{
                        "ticker": TICKER, "market_result": "no",
                        "yes_count_fp": "0.00", "no_count_fp": "0.11",
                    }],
                    "cursor": "",
                }
            if path == "/portfolio/fills":
                return {"fills": [matched_fill], "cursor": ""}
            raise AssertionError(path)

    row = enrich_history(
        [entry], client=FakeAccount(), require_settlement_counts=True,
    )[0]
    assert row["pnl_reason"] == "VERIFIED"
    assert row["pnl_status"] == "SETTLED"
    assert row["realized_pnl_dollars"] == "0.02"
