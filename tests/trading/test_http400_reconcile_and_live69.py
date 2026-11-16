"""Regression: exchange-confirmed fills outrank mistaken NOT_SUBMITTED UI labels.

All responses are synthetic. Never touches real Kalshi, credentials, or orders.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from kalshi_edge.bootstrap.final_forecast import (
    FinalForecastGate, MINIMUM_FINAL_DIRECTIONAL_PROBABILITY,
)
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.live_kalshi import LiveMarket
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.trading.autotrade_pnl import enrich_history
from kalshi_edge.trading.manual_trade import ManualTrader, OrderNotSubmitted


TICKER="KXBTC15M-26NOV161415-15"
CID="3b23c1c7-f4ef-4f0d-8b9a-9e53c61f1a0d"


def _record(outcome="DOWN", client_order_id=CID):
    return {
        "ticker":TICKER,"outcome":outcome,"amount":"3.00",
        "status":"NOT_SUBMITTED", "confidence":.76,
        "stages":[{
            "stage":"FINAL_BUY","outcome":outcome,"status":"NOT_SUBMITTED",
            "order_id":None,"fill_count":None,
            "quoted_count_fp":"3.74",
            "client_order_id":client_order_id,
        }],
    }


class ReadOnlyExchange:
    def __init__(self, *, client_id=CID, order_side="ask", count="3.74",
                 order_ticker=TICKER, exchange_online=True):
        self.client_id=client_id
        self.order_side=order_side
        self.count=count
        self.order_ticker=order_ticker
        self.exchange_online=exchange_online
        self.calls=[]

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        if not self.exchange_online:
            raise OSError("Offline")
        if path=="/portfolio/orders":
            return {
                "orders":[{
                    "client_order_id":self.client_id,"ticker":self.order_ticker,
                    "order_id":"verified-order-id",
                    "side":self.order_side,
                    "initial_count_fp":"3.74",
                    "fill_count_fp":self.count,
                    "status":"executed",
                }],
                "cursor":"",
            }
        if path=="/portfolio/settlements":
            return {"settlements":[{"ticker":TICKER,"market_result":"yes"}],
                    "cursor":""}
        if path=="/portfolio/fills":
            return {"fills":[{
                "order_id":"verified-order-id","ticker":TICKER,
                "count_fp":"3.74","yes_price_dollars":"0.2000",
                "no_price_dollars":"0.8000","fee_cost":"0.0080",
                "action":"buy","side":"no",
                "outcome_side":"no","book_side":"ask",
            }],"cursor":""}
        raise AssertionError(path)


def test_false_not_submitted_no_position_promoted_without_exact_exchange_order_identity():
    for kw in (
        {"client_id":"different-id"}, {"order_ticker":"WRONG-TICKER"},
        {"order_side":"bid"}, {"count":"0"},
        {"count":"3.75"},
    ):
        exchange=ReadOnlyExchange(**kw)
        result=enrich_history([_record()],exchange)[0]
        assert result["status"]=="NOT_SUBMITTED", kw
        assert result["realized_pnl_dollars"] is None, kw
        assert result["pnl_status"]=="UNAVAILABLE", kw
        assert not any(path.startswith("/portfolio/events/orders")
                       for path,_ in exchange.calls)


def test_exact_client_order_id_and_verified_no_fill_loses_three_dollars():
    exchange=ReadOnlyExchange()
    original=_record()
    results=enrich_history([original],exchange)
    result=results[0]
    assert original["status"]=="NOT_SUBMITTED"  # journal never rewritten
    assert result["status"]=="FILLED"
    assert Decimal(result["fill_count"])==Decimal("3.74")
    assert result["outcome"]=="DOWN"
    assert result["reconciliation_status"]=="EXCHANGE_ORDER_VERIFIED"
    assert result["pnl_status"]=="SETTLED"
    assert result["pnl_reason"]=="VERIFIED"
    assert Decimal(result["realized_pnl_dollars"]) < Decimal("-2.90")
    assert result["stages"][0]["order_id"]=="verified-order-id"
    assert result["stages"][0]["client_order_id"]==CID
    assert all(path in ("/portfolio/orders","/portfolio/settlements",
                        "/portfolio/fills")
               for path,_ in exchange.calls)


def test_offline_exchange_never_claims_no_order_or_zero_loss():
    result=enrich_history([_record()],ReadOnlyExchange(exchange_online=False))[0]
    assert result["pnl_status"]=="UNAVAILABLE"
    assert result["realized_pnl_dollars"] is None
    assert result["reconciliation_status"]=="EXCHANGE_NOT_VERIFIED"


def test_no_client_id_never_attributes_unrelated_market_fill_to_bot():
    exchange=ReadOnlyExchange()
    result=enrich_history([_record(client_order_id=None)],exchange)[0]
    assert result["status"]=="NOT_SUBMITTED"
    assert result["realized_pnl_dollars"] is None


def test_http_400_after_post_is_uncertain_until_readonly_reconciliation(tmp_path):
    class HTTP400:
        def send_order(self, order):
            request=httpx.Request("POST","https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders")
            response=httpx.Response(400,request=request)
            raise httpx.HTTPStatusError("Bad Request",request=request,response=response)
    trader=ManualTrader(tmp_path,vault=SimpleNamespace(),
                        client=HTTP400(),market_client=SimpleNamespace())
    price={
        "ticker":TICKER,"action":"BUY","outcome":"DOWN","units":"DOLLARS",
        "side":"ask","price":"0.2000","unit_price":"0.8000",
        "count":"3.74","fee_estimate":"0.0080",
        "amount_estimate":"3.0000","available_balance":"33.38",
        "position_yes":"0.00",
    }
    trader._prepare=lambda *args,fresh_account=False,**kw:dict(price)
    quote=trader.preview(TICKER,"BUY","DOWN","DOLLARS","3.00")
    with pytest.raises(Exception) as exc:
        trader.place_auto(quote["preview_id"],TICKER,client_order_id=CID)
    assert not isinstance(exc.value,OrderNotSubmitted)
    assert "400" in type(exc.value).__name__ or isinstance(exc.value,httpx.HTTPStatusError)


def test_69_percent_gate_is_on_predicted_side_probability_not_strength():
    assert MINIMUM_FINAL_DIRECTIONAL_PROBABILITY==.69
    close=1_900_000_000_000_000_000
    market=LiveMarket(ticker=TICKER,strike=65000.,
        open_ts_ns=close-900_000_000_000,close_ts_ns=close)
    scheduled=close-480_000_000_000
    def decision(p):
        prediction=LivePredictionRecord(
            market_ticker=TICKER,prediction_ts_ns=scheduled,status="OK",
            p_yes=p,p_no=1-p,predicted_side="ABOVE" if p>=.5 else "BELOW",
            feed_quality=FeedQuality(healthy=True),
            confidence=2*abs(p-.5),
            final_probability=p,
            model_hash="synthetic-test-model",
        )
        return FinalForecastGate().evaluate(market,now_ns=scheduled,prediction=prediction)
    assert decision(.68).forecast=="SKIP"
    assert decision(.69).forecast=="SKIP"
    assert decision(.69).reason=="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_69_PERCENT"
    assert decision(.691).forecast=="ABOVE"
    assert decision(.81).forecast=="ABOVE"  # 62% strength does NOT veto a valid >69% probability


def test_new_training_gate_uses_predicted_side_probability_not_legacy_strength():
    from kalshi_edge.bootstrap.evaluate import checkpoint_gate_snapshot
    labels=[1,1,1,0]
    probabilities=[.68,.69,.70,.30]
    report=checkpoint_gate_snapshot(
        labels,probabilities,
        minimum_confidence=.69,
        gate_kind="predicted_side_probability",
    )
    assert report.gate_kind=="predicted_side_probability"
    assert report.qualified_markets==2
    assert report.qualified_correct==2


def test_new_paired_lockbox_counts_use_same_strict_69_probability_as_final():
    from kalshi_edge.bootstrap.promotion_audit import paired_480_evidence
    market=["KXBTC15M-TEST-"+str(i) for i in range(4)]
    report=paired_480_evidence(
        market,[1,1,1,0],
        [.68,.69,.70,.30],[.69,.69,.69,.31],
        minimum_confidence=.69,gate_kind="predicted_side_probability",
        resamples=5,
    )
    assert report["gate_kind"]=="predicted_side_probability"
    assert report["candidate_qualified"]==2
    assert report["incumbent_qualified"]==0


def test_ui_marks_unverified_exchange_rejection_never_as_skipped_or_no_trade():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    page=integrate_manual_trade(PAGE)
    assert 'case "NOT_SUBMITTED":return "NOT SENT"' in page
    assert 'entry.reconciliation_status==="EXCHANGE_ORDER_VERIFIED"' in page
    assert '?"VERIFY KALSHI":autoHistoryStatusLabel(entry.status);' in page
    assert "more than 69% predicted-direction probability" in page
    assert "Current live gate: >69%" in page
