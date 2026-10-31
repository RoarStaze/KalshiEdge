"""Manual Kalshi order card: simulated HTTP transports only; never posts to exchange."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest

from kalshi_edge.trading.manual_trade import quote_from_book, TradingError, KalshiOrderClient, ManualTrader


def market():
    return {"market":{"ticker":"KXBTC15M-TEST","status":"active",
                      "fractional_trading_enabled":True}}

def orderbook():
    return {"orderbook_fp":{"yes_dollars":[["0.3000","5.00"]],
                            "no_dollars":[["0.2900","4.00"]]}}

@pytest.mark.parametrize("action,outcome,expected_yes_price,expected_book,expected_effective",[
    ("BUY","UP","0.7100","bid","0.7100"),
    ("BUY","DOWN","0.3000","ask","0.7000"),
    ("SELL","UP","0.3000","ask","0.3000"),
    ("SELL","DOWN","0.7100","bid","0.2900"),
])
def test_manual_quote_book_mapping(action,outcome,expected_yes_price,expected_book,expected_effective):
    q=quote_from_book(orderbook(),action,outcome)
    assert str(q["yes_price"])==expected_yes_price
    assert q["book_side"]==expected_book
    assert str(q["unit_cash"])==expected_effective

def test_no_buy_without_balance_or_sell_without_position(tmp_path):
    trader=ManualTrader(tmp_path, vault=SimpleNamespace(exists=lambda:True),client=None)
    with pytest.raises(TradingError):
        trader.size_order(Decimal("0.01"),"BUY","UP","DOLLARS",Decimal("2"),Decimal("0"),
                          orderbook(),market())
    with pytest.raises(TradingError):
        trader.size_order(Decimal("10.00"),"SELL","UP","SHARES",Decimal("1"),Decimal("0"),
                          orderbook(),market())

def test_order_client_only_posts_with_explicit_send_call():
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=="POST":
            return httpx.Response(201,json={
                "order_id":"exchange123", "client_order_id":"id123",
                "fill_count":"0.16","remaining_count":"0.00","ts_ms":1790000000
            })
        return httpx.Response(200,json={"balance_dollars":"3.14"})
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    key=ed25519.Ed25519PrivateKey.generate()
    pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    vault=SimpleNamespace(load=lambda:("demo-key",pem))
    client=KalshiOrderClient(vault,transport=httpx.MockTransport(handler))
    assert client.get("/portfolio/balance")["balance_dollars"]=="3.14"
    assert len(calls)==1 and calls[0].method=="GET"
    reply=client.send_order({"ticker":"KXBTC15M-TEST","client_order_id":"id123","side":"ask","count":"0.16","price":"0.3000","reduce_only":False})
    assert reply["order_id"]=="exchange123"
    assert len(calls)==2 and calls[1].url.path.endswith("/portfolio/events/orders")
    assert b'"immediate_or_cancel"' in calls[1].read()
    client.close()

def test_dashboard_hides_old_cards_without_breaking_live_dom_ids():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    page=integrate_manual_trade(PAGE)
    assert "Market snapshot" in page and "Prediction status" in page
    assert ".context>.context-header,.context>.health{display:none!important}" in page
    assert 'id="manual-buy"' in page and 'id="manual-sell"' in page
    assert 'id="manual-submit"' in page
    assert 'id="strike"' in page
    assert 'id="market-ticker"' in page
