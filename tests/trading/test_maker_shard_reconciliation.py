"""Regression: accepted BTC maker orders must be reconciled across Kalshi shards.

The Kalshi Get Orders API supports filtering by ticker and returns orders from
all shards when exchange_index is omitted. These are offline fakes only.
"""
from decimal import Decimal

import pytest

from kalshi_edge.trading.maker_entry import finalize_resting_order

TICKER = "KXBTC15M-SHARD-TEST"
ORDER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


class ShardedOrderClient:
    def __init__(self, *, matched=True, initially_resting=False):
        self.matched = matched
        self.initially_resting = initially_resting
        self.cancelled = []
        self.reads = []

    def get(self, path, *, params=None):
        self.reads.append((path, params))
        # A GET by ID alone incorrectly defaults to the wrong exchange shard.
        assert path == "/portfolio/orders"
        assert params == {"ticker": TICKER, "limit": 1000}
        orders = [
            {"order_id": OTHER_ID, "ticker": TICKER,
             "status": "canceled", "fill_count_fp": "0.00"},
        ]
        if self.matched:
            orders.append({
                "order_id": ORDER_ID, "ticker": TICKER,
                "status": ("resting" if self.initially_resting and not self.cancelled
                           else "canceled" if self.initially_resting else "executed"),
                "fill_count_fp": "0.00" if self.initially_resting else "0.12",
            })
        return {"orders": orders, "cursor": ""}

    def cancel_resting_order(self, order_id, *, market_ticker):
        assert order_id == ORDER_ID
        assert market_ticker == TICKER
        self.cancelled.append(order_id)
        return {"order_id": order_id}


def test_crypto_maker_reconciles_by_market_and_exact_order_id():
    client = ShardedOrderClient()
    result = finalize_resting_order(
        client, ORDER_ID, TICKER, Decimal("0.12"),
        poll_seconds=0, sleep=lambda _: None,
    )
    assert result == {"status": "FILLED", "order_id": ORDER_ID, "fill_count": "0.12"}
    assert client.cancelled == []
    assert len(client.reads) == 1


def test_market_order_list_without_the_exact_id_cannot_fabricate_zero_fill():
    client = ShardedOrderClient(matched=False)
    with pytest.raises(ValueError, match="unconfirmed"):
        finalize_resting_order(
            client, ORDER_ID, TICKER, Decimal("0.12"),
            poll_seconds=0, sleep=lambda _: None,
        )
    assert client.cancelled == [ORDER_ID]
    assert len(client.reads) == 2


def test_unfilled_order_requires_confirmed_cancel_on_same_market():
    client = ShardedOrderClient(initially_resting=True)
    result = finalize_resting_order(
        client, ORDER_ID, TICKER, Decimal("0.12"),
        poll_seconds=0, sleep=lambda _: None,
    )
    assert result["status"] == "NO_FILL"
    assert result["fill_count"] == "0.00"
    assert client.cancelled == [ORDER_ID]
    assert len(client.reads) == 2


def test_malformed_exchange_order_list_is_unknown_not_no_fill():
    class Invalid:
        def get(self, path, *, params=None):
            return {"orders": "invalid"}
        def cancel_resting_order(self, *_args, **_kwargs):
            raise AssertionError("Never cancel without reliable order lookup")
    with pytest.raises(ValueError, match="order"):
        finalize_resting_order(
            Invalid(), ORDER_ID, TICKER, Decimal("0.12"),
            poll_seconds=0, sleep=lambda _: None,
        )
