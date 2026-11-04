"""Post-only maker entry tests. No network or real account."""
from decimal import Decimal

from kalshi_edge.trading.maker_entry import build_maker_plan, finalize_resting_order


def book():
    return {"orderbook_fp": {
        "yes_dollars": [["0.7800", "1.00"]],
        "no_dollars": [["0.1800", "1.00"]],
    }}


def test_up_maker_joins_bid_and_earns_better_purchase_price():
    plan = build_maker_plan(
        book(), "UP", count="0.12", requested="0.10",
        balance="3.54", confidence=0.98, ask_price="0.8200",
    )
    assert plan["side"] == "bid"
    assert plan["price"] == "0.7800"
    assert Decimal(plan["amount_estimate"]) < Decimal("0.10")


def test_down_maker_posts_yes_ask_at_no_bid():
    plan = build_maker_plan(
        book(), "DOWN", count="0.12", requested="0.10",
        balance="3.54", confidence=0.98, ask_price="0.8200",
    )
    assert plan["side"] == "ask"
    assert plan["price"] == "0.8200"
    assert plan["unit_price"] == "0.1800"


def test_maker_refuses_quote_without_positive_edge():
    assert build_maker_plan(
        book(), "UP", count="0.12", requested="0.10",
        balance="3.54", confidence=0.69, ask_price="0.8200",
    ) is None


def test_maker_only_retries_after_verified_zero_fill():
    class Exchange:
        cancelled = []
        def get(self, path, *, params=None):
            return {"order": {"status": "canceled", "fill_count_fp": "0.00",
                              "ticker": "KXBTC15M-TEST"}}
        def cancel_resting_order(self, order_id):
            self.cancelled.append(order_id)
            return {"order_id": order_id}
    client = Exchange()
    result = finalize_resting_order(
        client, "11111111-1111-4111-8111-111111111111",
        "KXBTC15M-TEST", Decimal("0.12"),
        poll_seconds=0, sleep=lambda _: None,
    )
    assert result["status"] == "NO_FILL"
    assert result["fill_count"] == "0.00"
    assert client.cancelled == []


def test_maker_unknown_order_state_never_assumed_zero_fill():
    class Exchange:
        def get(self, path, *, params=None):
            return {"order": {"status": "resting", "fill_count_fp": "0.00",
                              "ticker": "KXBTC15M-TEST"}}
        def cancel_resting_order(self, order_id):
            return {"order_id": order_id}
    try:
        finalize_resting_order(
            Exchange(), "11111111-1111-4111-8111-111111111111",
            "KXBTC15M-TEST", Decimal("0.12"),
            poll_seconds=0, sleep=lambda _: None,
        )
    except ValueError as exc:
        assert "unconfirmed" in str(exc)
    else:
        raise AssertionError("Resting order state must never be treated as zero fill")
