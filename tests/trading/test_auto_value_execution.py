"""Real ManualTrader pre-submit tests; all Kalshi exchange calls are mocked."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.trading.manual_trade import ManualTrader, OrderNotSubmitted


class FakeExchange:
    def __init__(self):
        self.orders = []
    def send_order(self, payload):
        self.orders.append(dict(payload))
        return {
            "order_id": "offline-order",
            "fill_count": payload["count"],
        }


def _plan(unit, count="0.12", cash="3.54"):
    from decimal import Decimal
    fee = Decimal("0.0030")
    debit = Decimal(unit) * Decimal(count) + fee
    return {
        "ticker": "KXBTC15M-VALUE-REPRICE", "action": "BUY",
        "outcome": "UP", "units": "DOLLARS", "side": "bid",
        "price": unit, "unit_price": unit, "count": count,
        "fee_estimate": format(fee, ".6f"),
        "amount_estimate": format(debit, ".4f"),
        "available_balance": cash, "position_yes": "0.00",
    }


def _setup(tmp_path, old, fresh):
    client = FakeExchange()
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(),
                          client=client, market_client=SimpleNamespace())
    trader._prepare = lambda *args, fresh_account=False, **kwargs: dict(
        fresh if fresh_account else old
    )
    preview = trader.preview("KXBTC15M-VALUE-REPRICE", "BUY", "UP", "DOLLARS", "0.10")
    return trader, client, preview


def test_profitable_fresh_price_submits_at_most_once(tmp_path):
    trader, client, preview = _setup(tmp_path, _plan("0.8000"), _plan("0.7500"))
    result = trader.place_auto_value(
        preview["preview_id"], "KXBTC15M-VALUE-REPRICE", confidence=0.98
    )
    assert result["status"] == "FILLED"
    assert len(client.orders) == 1
    assert client.orders[0]["price"] == "0.7500"
    with pytest.raises(OrderNotSubmitted):
        trader.place_auto_value(
            preview["preview_id"], "KXBTC15M-VALUE-REPRICE", confidence=0.98
        )
    assert len(client.orders) == 1


def test_worsening_price_cannot_override_approved_preview(tmp_path):
    trader, client, preview = _setup(tmp_path, _plan("0.8000"), _plan("0.8200", count="0.11"))
    with pytest.raises(OrderNotSubmitted, match="worsened"):
        trader.place_auto_value(
            preview["preview_id"], "KXBTC15M-VALUE-REPRICE", confidence=0.98
        )
    assert client.orders == []


def test_fresh_quote_cannot_risk_over_five_percent_of_cash(tmp_path):
    trader, client, preview = _setup(
        tmp_path, _plan("0.8000"), _plan("0.7500", cash="1.00")
    )
    with pytest.raises(OrderNotSubmitted, match="5%"):
        trader.place_auto_value(
            preview["preview_id"], "KXBTC15M-VALUE-REPRICE", confidence=0.98
        )
    assert client.orders == []
