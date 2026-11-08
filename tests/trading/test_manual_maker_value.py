"""Integration of short-expiry maker into ManualTrader (no live network)."""
from types import SimpleNamespace
import uuid

from kalshi_edge.trading.manual_trade import ManualTrader


def test_first_value_order_uses_expiring_post_only_when_passive_bid_is_available(tmp_path):
    ticker = "KXBTC15M-MAKER-EXAMPLE"
    order_id = str(uuid.uuid4())

    class Client:
        def __init__(self):
            self.makers = []
            self.iocs = []
        def send_resting_order(self, order, *, expiration_time):
            self.makers.append((dict(order), expiration_time))
            return {"order_id": order_id, "fill_count": "0.00"}
        def send_order(self, order):
            self.iocs.append(dict(order))
            return {"order_id": order_id, "fill_count": order["count"]}
        def get(self, path, *, params=None):
            return {"orders": [{"order_id": order_id, "ticker": ticker,
                                "status": "canceled", "fill_count_fp": "0.00"}], "cursor": ""}
        def cancel_resting_order(self, order, *, market_ticker):
            return {"order_id": order}
    client = Client()
    book = {"orderbook_fp": {
        "yes_dollars": [["0.7800", "1.00"]],
        "no_dollars": [["0.1800", "1.00"]],
    }}
    trader = ManualTrader(tmp_path, vault=SimpleNamespace(), client=client,
                          market_client=SimpleNamespace(
                              quote=lambda _: ({"market": {"ticker": ticker}}, book)))
    plan = {"ticker": ticker, "action": "BUY", "outcome": "UP", "units": "DOLLARS",
            "side": "bid", "price": "0.8200", "unit_price": "0.8200",
            "count": "0.12", "fee_estimate": "0.0010",
            "amount_estimate": "0.0994", "available_balance": "3.54",
            "position_yes": "0.00"}
    trader._prepare = lambda *args, fresh_account=False, **kwargs: dict(plan)
    preview = trader.preview(ticker, "BUY", "UP", "DOLLARS", "0.10")
    result = trader.place_auto_value(
        preview["preview_id"], ticker, confidence=0.98, prefer_maker=True
    )
    assert result["status"] == "NO_FILL"
    assert client.iocs == []
    assert len(client.makers) == 1
    assert client.makers[0][0]["price"] == "0.7800"
    assert client.makers[0][0]["side"] == "bid"
    assert result["order_id"] == order_id
