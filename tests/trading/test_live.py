"""Real-order tests: no network, no simulated fills or monetary mutations."""
from decimal import Decimal
from types import SimpleNamespace
import httpx
import pytest

from kalshi_edge.trading.live import AuthenticatedKalshiClient, LiveLedger, LiveTrader, live_entry_plan, LiveOrderError
from kalshi_edge.trading.risk import EntryRejected

NS=1_000_000_000
TS=1_800_000_000_000_000_000
TICKER="KXBTC15M-TEST"
def forecast(side="ABOVE"):
    return SimpleNamespace(
        market_ticker=TICKER, forecast=side,
        scheduled_ts_ns=TS, prediction_ts_ns=TS,
        confidence=.94, directional_probability=.94, strike=80_000.0,
    )
def market():
    from datetime import datetime, timezone
    return {"market":{"ticker":TICKER,"status":"active","floor_strike":80000.0,
                      "close_time":datetime.fromtimestamp((TS+240*NS)/NS,timezone.utc).isoformat()}}
def book():
    return {"orderbook_fp":{"yes_dollars":[["0.3000","10.00"]],
                             "no_dollars":[["0.3000","10.00"]]}}

def test_live_plan_is_whole_contracts_not_paper_fractional():
    yes=live_entry_plan(forecast(),market(),book(),TS+NS,Decimal("10"),5)
    assert yes["side"] == "bid"
    assert yes["price"] == "0.7000"
    assert yes["count"] == "5.00"
    no=live_entry_plan(forecast("BELOW"),market(),book(),TS+NS,Decimal("10"),1)
    assert no["side"] == "ask"
    assert no["price"] == "0.3000"
    assert no["count"] == "1.00"
    assert no["outcome"] == "BELOW"

def test_live_plan_refuses_stale_skip_unfunded_or_no_edge():
    with pytest.raises(EntryRejected):
        live_entry_plan(forecast(),market(),book(),TS+21*NS,Decimal("10"),1)
    with pytest.raises(EntryRejected):
        live_entry_plan(forecast(),market(),book(),TS+NS,Decimal("0.10"),1)
    with pytest.raises(EntryRejected):
        live_entry_plan(forecast("SKIP"),market(),book(),TS+NS,Decimal("10"),1)

def test_ledger_is_durable_and_blocks_repeated_submissions(tmp_path):
    ledger=LiveLedger(tmp_path/"live.sqlite3")
    assert ledger.reserve(TICKER,"ABOVE",TS,"stable-id")
    assert not LiveLedger(tmp_path/"live.sqlite3").reserve(TICKER,"ABOVE",TS+NS,"other-id")
    ledger.finish(TICKER,"FILLED",order_id="exchange-order",fill_count="1.00",price="0.7000",quantity="1.00")
    assert ledger.snapshot()[0]["order_id"] == "exchange-order"

def test_auto_trader_disarmed_by_default_even_with_saved_credentials(tmp_path):
    class KeyVault:
        def exists(self): return True
    class NeverAPI:
        def balance(self): raise AssertionError("no network without enable")
    trader=LiveTrader(tmp_path,vault=KeyVault(),api=NeverAPI())
    assert not trader.enabled()
    assert trader.status()["mode"]=="LIVE"
    assert trader.status()["enabled"] is False
    trader.poll_once(now_ns=TS+NS)
    assert trader.ledger.snapshot()==[]

def test_auth_signing_ed25519_and_order_v2_payload():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    private=ed25519.Ed25519PrivateKey.generate()
    pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/portfolio/balance"):
            return httpx.Response(200,json={"balance_dollars":"25.0000"})
        return httpx.Response(201,json={"order_id":"abc","client_order_id":"cid",
                                        "fill_count":"1.00","remaining_count":"0.00"})
    class Vault:
        def load(self):return "test-id",pem
    api=AuthenticatedKalshiClient(Vault(),transport=httpx.MockTransport(handler))
    try:
        assert api.balance()==Decimal("25.0000")
        result=api.create_order("cid",TICKER,{"side":"ask","count":"1.00","price":"0.3000"})
        assert result["order_id"]=="abc"
        assert [x.method for x in calls]==["GET","POST"]
        assert calls[-1].url.path=="/trade-api/v2/portfolio/events/orders"
        assert calls[-1].read().count(b'"time_in_force"')==1
        assert b'"immediate_or_cancel"' in calls[-1].read()
        assert b'"side":"ask"' in calls[-1].read()
        assert calls[-1].headers["KALSHI-ACCESS-KEY"]=="test-id"
    finally:
        api.close()
