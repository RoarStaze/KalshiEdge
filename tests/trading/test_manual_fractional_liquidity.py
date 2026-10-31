"""Manual KXBTC15M no-liquidity display and small dollar order regression tests.

All market/book and portfolio interactions are simulated. No real orders sent.
"""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest

from kalshi_edge.trading.manual_trade import (
    ManualTrader, TradingError, NoLiquidityError, quote_from_book,
)
from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
from kalshi_edge.bootstrap.dashboard_ui import PAGE


def market(*, fractional="missing"):
    payload = {"ticker":"KXBTC15M-TEST", "status":"active",
               "close_time":"2099-01-01T00:00:00Z"}
    if fractional != "missing":
        payload["fractional_trading_enabled"] = fractional
    return {"market":payload}


def book(*, yes="0.4000", no="0.3900", qty="20.00"):
    return {"orderbook_fp":{
        "yes_dollars":[] if yes is None else [[yes,qty]],
        "no_dollars":[] if no is None else [[no,qty]],
    }}


def test_ten_cent_buy_accepts_fractional_contracts_when_flag_omitted():
    # Public GET market responses can omit this capability flag.
    # Do not silently demand a whole contract on those markets.
    result=ManualTrader.size_order(
        None,Decimal("3.16"),"BUY","DOWN","DOLLARS",Decimal("0.10"),
        Decimal("0"),book(yes="0.4000"),market())
    assert Decimal("0.01")<=Decimal(result["count"])<Decimal("1")
    assert Decimal(result["amount_estimate"])<=Decimal("0.10")
    assert Decimal(result["maximum_payout"])==Decimal(result["count"])


def test_explicit_fractional_restriction_does_not_allow_ten_cent_buy():
    with pytest.raises(TradingError,match="share increment|fractional"):
        ManualTrader.size_order(
            None,Decimal("3.16"),"BUY","DOWN","DOLLARS",Decimal("0.10"),
            Decimal("0"),book(yes="0.4000"),market(fractional=False))


def test_buy_up_has_no_liquidity_but_down_quote_remains_available(tmp_path):
    data=book(no=None,yes="0.4000")
    def quote(ticker):
        return market()["market"] and market(), data
    class Account:
        def get(self,path,*,params=None):
            if path=="/portfolio/balance":return {"balance_dollars":"3.16"}
            if path=="/portfolio/positions":return {"market_positions":[]}
            raise AssertionError(path)
        def close(self):pass
    trader=ManualTrader(tmp_path,vault=SimpleNamespace(exists=lambda:True),
                       client=Account(),market_client=SimpleNamespace(quote=quote,close=lambda:None))
    values=trader.market("KXBTC15M-TEST")
    assert values["up_ask"] is None
    assert values["down_ask"]=="0.6000"
    with pytest.raises(NoLiquidityError,match="No liquidity"):
        trader.preview("KXBTC15M-TEST","BUY","UP","DOLLARS","0.10")
    down=trader.preview("KXBTC15M-TEST","BUY","DOWN","DOLLARS","0.10")
    assert Decimal(down["count"])>=Decimal("0.01")


def test_malformed_orderbook_is_not_misrepresented_as_no_liquidity():
    with pytest.raises(TradingError,match="orderbook"):
        quote_from_book({}, "BUY", "UP")
    with pytest.raises(NoLiquidityError,match="No liquidity"):
        quote_from_book(book(no=None), "BUY", "UP")


def test_empty_book_does_not_create_executable_preview():
    with pytest.raises(NoLiquidityError,match="No liquidity"):
        ManualTrader.size_order(
            None,Decimal("3.16"),"BUY","UP","DOLLARS",Decimal("0.10"),
            Decimal("0"),book(no=None),market())


def test_ui_has_no_liquidity_state_and_null_quote_protection():
    page=integrate_manual_trade(PAGE)
    assert 'id="manual-liquidity"' in page
    assert 'No liquidity' in page
    assert 'value.up_ask==null' in page
    assert 'value.down_ask==null' in page
    assert "Selected side has no liquidity" in page
