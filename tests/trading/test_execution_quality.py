"""Only exchange-matched fills can be compared with immutable AutoTrade quote previews."""
from __future__ import annotations

from decimal import Decimal

import pytest

from kalshi_edge.trading.autotrade_pnl import enrich_history
from kalshi_edge.trading.execution_quality import measure_execution_quality


TICKER = "KXBTC15M-QUALITYTEST"


def stage(stage_name, order, *, side="UP", qty="0.25", q_count="0.25",
          q_price="0.4000", q_fee="0.006000"):
    return {
        "ticker": TICKER,
        "stage": stage_name,
        "status": "FILLED",
        "outcome": side,
        "order_id": order,
        "fill_count": qty,
        "quoted_unit_price_dollars": q_price,
        "quoted_count_fp": q_count,
        "quoted_fee_dollars": q_fee,
        "quote_gate": "SELL_UNGATED" if stage_name=="REVERSAL_SELL" else "APPROVED",
        "preview_received_at_ns": 1_900_000_000_000_000_000,
    }


def fill(order, *, side="UP", action="buy", qty="0.25", price="0.4300",
         fee="0.007000", identity="fill-1"):
    price_value = Decimal(price)
    return {
        "fill_id": identity, "ticker": TICKER, "market_ticker": TICKER,
        "order_id": order, "action": action,
        "side": "yes" if side=="UP" else "no",
        "count_fp": qty, "yes_price_dollars":
            str(price_value if side=="UP" else 1-price_value),
        "no_price_dollars":
            str(price_value if side=="DOWN" else 1-price_value),
        "fee_cost": fee,
    }


def row(*stages):
    return {
        "ticker": TICKER, "attempted_at_ns":1_900_000_000_000_000_000,
        "outcome": stages[0]["outcome"],
        "amount":"1.00", "confidence":.9,
        "status":stages[0]["status"],
        "order_id":stages[0]["order_id"],
        "fill_count":stages[0]["fill_count"],
        "stages":list(stages),
    }


class Exchange:
    def __init__(self, fills, result="yes"):
        self.fills=fills
        self.result=result
    def get(self, path, params=None):
        if path=="/portfolio/settlements":
            return {"settlements":[{"ticker":TICKER,
                                    "market_result":self.result}]}
        if path=="/portfolio/fills":
            return {"fills":self.fills}
        raise AssertionError(path)


def test_buy_all_in_slippage_uses_actual_fee_and_quote_fee_per_share():
    s=stage("FINAL_BUY","buy")
    measured=measure_execution_quality([s],[fill("buy")],TICKER)
    assert measured["status"]=="VERIFIED_QUOTE_TO_FILL"
    assert measured["stages_with_comparable_quotes"]==1
    value=measured["stages"][0]
    assert value["quoted_unit_price_dollars"]=="0.400000"
    assert value["actual_weighted_fill_price_dollars"]=="0.430000"
    assert value["estimated_quote_fee_per_contract_dollars"]=="0.024000"
    assert value["actual_fill_fee_per_contract_dollars"]=="0.028000"
    assert value["adverse_all_in_cost_per_contract_dollars"]=="0.034000"
    assert value["adverse_total_for_filled_shares_dollars"]=="0.008500"
    assert value["action"]=="BUY"


def test_reversal_sell_low_price_and_higher_fee_means_adverse_cost():
    stages=[
        stage("EARLY_BUY","early",q_price="0.4000",q_fee="0.006000"),
        stage("REVERSAL_SELL","sell",q_price="0.6000",q_fee="0.006000"),
        stage("FINAL_BUY","final",side="DOWN",q_price="0.4000",q_fee="0.006000"),
    ]
    fills=[
        fill("early",identity="a"),
        fill("sell",action="sell",price="0.5700",identity="b"),
        fill("final",side="DOWN",price="0.4000",fee="0.006000",identity="c"),
    ]
    measured=measure_execution_quality(stages,fills,TICKER)
    assert measured["status"]=="VERIFIED_QUOTE_TO_FILL"
    reversal=next(s for s in measured["stages"] if s["stage"]=="REVERSAL_SELL")
    assert reversal["action"]=="SELL"
    assert reversal["adverse_all_in_cost_per_contract_dollars"]=="0.034000"


def test_partial_fill_compares_per_share_not_unfilled_full_order():
    s=stage("FINAL_BUY","buy",qty="0.25",q_count="0.50")
    quality=measure_execution_quality([s],[fill("buy")],TICKER)["stages"][0]
    assert quality["quote_count_fp"]=="0.50"
    assert quality["filled_count_fp"]=="0.25"
    assert quality["fill_fraction_of_quote"]=="0.500000"
    assert quality["adverse_total_for_filled_shares_dollars"]=="0.008500"


def test_many_fills_correct_weighted_execution_average():
    s=stage("FINAL_BUY","buy",qty="0.20",q_count="0.20",q_fee="0.000000")
    fills=[
        fill("buy",qty="0.10",price="0.4000",fee="0",identity="a"),
        fill("buy",qty="0.10",price="0.5000",fee="0",identity="b"),
    ]
    quality=measure_execution_quality([s],fills,TICKER)["stages"][0]
    assert quality["actual_weighted_fill_price_dollars"]=="0.450000"
    assert quality["adverse_all_in_cost_per_contract_dollars"]=="0.050000"


def test_legacy_no_quote_returns_missing_not_fake_zero_slippage():
    s=stage("FINAL_BUY","old")
    s["quoted_unit_price_dollars"]=None
    measured=measure_execution_quality([s],[fill("old")],TICKER)
    assert measured["status"]=="MISSING_QUOTE"
    assert measured["stages_with_comparable_quotes"]==0
    assert measured["stages"]==[]


def test_oversized_exchange_fill_or_wrong_ticker_fails_quality_without_claiming_value():
    s=stage("FINAL_BUY","buy")
    x=fill("buy",qty="0.30")
    assert measure_execution_quality([s],[x],TICKER)["status"]=="UNAVAILABLE"
    x=fill("buy")
    x["ticker"]="KXBTC15M-WRONG"
    assert measure_execution_quality([s],[x],TICKER)["status"]=="UNAVAILABLE"


def test_enrich_history_adds_quality_only_with_verified_settlement_and_matching_fills():
    s=stage("FINAL_BUY","buy")
    enriched=enrich_history([row(s)], Exchange([fill("buy")]))[0]
    assert enriched["pnl_status"]=="SETTLED"
    assert enriched["execution_quality"]["status"]=="VERIFIED_QUOTE_TO_FILL"
    assert enriched["execution_quality"]["stages"][0][
        "adverse_all_in_cost_per_contract_dollars"]=="0.034000"


def test_unverified_fill_prohibits_quality_even_when_quote_known():
    s=stage("FINAL_BUY","buy")
    invalid=fill("buy")
    invalid["side"]="no"
    enriched=enrich_history([row(s)], Exchange([invalid]))[0]
    assert enriched["pnl_status"]=="UNAVAILABLE"
    assert enriched["execution_quality"] is None


def test_all_legacy_stages_do_not_block_verified_pnl():
    s=stage("FINAL_BUY","old")
    s["quoted_unit_price_dollars"]=None
    enriched=enrich_history([row(s)], Exchange([fill("old")]))[0]
    assert enriched["pnl_status"]=="SETTLED"
    assert enriched["execution_quality"]["status"]=="MISSING_QUOTE"
