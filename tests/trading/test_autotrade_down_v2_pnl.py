"""Economic identity and fee-inclusive P/L for V2 YES-book DOWN fills.

This regression uses synthetic fills only, never authenticated accounts.
"""
from __future__ import annotations

import pytest

from kalshi_edge.trading.autotrade_pnl import enrich_history
from kalshi_edge.trading.execution_quality import measure_execution_quality

TICKER="KXBTC15M-26NOV041145-45"


def history(side="DOWN", qty="0.11", *, quote=None):
    stage={"stage":"FINAL_BUY","outcome":side,"fill_count":qty,
           "order_id":"order-no-1","status":"FILLED"}
    if quote is not None:
        stage.update(quote)
    return [{"ticker":TICKER,"status":"FILLED","outcome":side,"amount":"0.10",
             "stages":[stage]}]


def v2_fill(*, outcome_side="yes", book_side="ask", yes="0.2000", no="0.8000",
            qty="0.11", fee="0.0020", order="order-no-1", **overrides):
    f={
        "fill_id":"fill-no-1","ticker":TICKER,"order_id":order,
        "outcome_side":outcome_side,"book_side":book_side,
        "count_fp":qty,"yes_price_dollars":yes,"no_price_dollars":no,
        "fee_cost":fee,
    }
    f.update(overrides)
    return f


class Client:
    def __init__(self, fills, result="no"):
        self.fills=fills
        self.result=result
        self.calls=[]

    def get(self, path, *, params=None):
        self.calls.append((path,params))
        if path=="/portfolio/settlements":
            return {"settlements":[{"ticker":TICKER,"market_result":self.result}],
                    "cursor":""}
        if path=="/portfolio/fills":
            return {"fills":self.fills,"cursor":""}
        raise AssertionError(path)


@pytest.mark.parametrize("result,expected",[
    ("no","0.0200"),
    ("yes","-0.09"),
])
def test_down_buy_as_yes_ask_reconciles_realized_fees_for_both_outcomes(result,expected):
    f=v2_fill(yes="0.2000",no="0.8000",fee="0.0020")
    # DOWN BUY is an ASK on YES, not always a legacy BUY on NO.
    row=enrich_history(history(),Client([f],result))[0]
    assert row["status"]=="FILLED"
    assert row["outcome"]=="DOWN"
    assert row["pnl_status"]=="SETTLED"
    assert row["realized_pnl_dollars"]==expected


def test_down_buy_no_bid_is_same_economic_action():
    # Some V2 representations may express BUY NO as NO/BID.
    f=v2_fill(outcome_side="no",book_side="bid")
    row=enrich_history(history(),Client([f],"no"))[0]
    assert row["realized_pnl_dollars"]=="0.0200"
    assert row["pnl_status"]=="SETTLED"


def test_down_legacy_yes_sell_is_accepted_only_when_equivalent():
    f=v2_fill(side="yes",action="sell")
    row=enrich_history(history(),Client([f],"no"))[0]
    assert row["realized_pnl_dollars"]=="0.0200"


def test_down_legacy_no_buy_is_accepted_only_when_equivalent():
    f=v2_fill(side="no",action="buy")
    row=enrich_history(history(),Client([f],"no"))[0]
    assert row["realized_pnl_dollars"]=="0.0200"


def test_up_v2_yes_bid_reconciles_normal_buy():
    f=v2_fill(outcome_side="yes",book_side="bid",yes="0.7000",no="0.3000")
    row=enrich_history(history("UP"),Client([f],"yes"))[0]
    # 0.11 - 0.11*0.70 - 0.002 = 0.031
    assert row["realized_pnl_dollars"]=="0.0310"


@pytest.mark.parametrize("missing_field",["yes_price_dollars","no_price_dollars"])
def test_can_reconcile_with_only_one_authentic_binary_price(missing_field):
    f=v2_fill()
    del f[missing_field]
    row=enrich_history(history(),Client([f],"no"))[0]
    assert row["realized_pnl_dollars"]=="0.0200"


@pytest.mark.parametrize("patch",[
    {"outcome_side":"yes","book_side":"bid"},    # BUY UP != BUY DOWN
    {"outcome_side":"yes","book_side":"ask","side":"yes","action":"buy"},
    {"outcome_side":"yes","book_side":"ask","side":"no","action":"sell"},
    {"outcome_side":"invalid"},
    {"book_side":"invalid"},
    {"outcome_side":None},
    {"book_side":None},
    {"yes_price_dollars":"0.2000","no_price_dollars":"0.7500"}, # inconsistent prices
    {"fee_cost":None},
    {"fee_cost":"-0.01"},
    {"count_fp":"0.10"},                           # fractional count mismatch
    {"ticker":"KXBTC15M-WRONG"},
    {"order_id":"manual-in-same-market"},
])
def test_down_never_fabricates_profit_for_mismatched_or_incomplete_v2_fill(patch):
    f=v2_fill(**patch)
    row=enrich_history(history(),Client([f],"no"))[0]
    assert row["realized_pnl_dollars"] is None
    assert row["pnl_status"] in ("UNAVAILABLE","MIXED")


def test_mixed_manual_fill_on_same_market_still_refuses_attribution():
    expected=v2_fill()
    manual=v2_fill(order="manual-order",fill_id="fill-manual")
    row=enrich_history(history(),Client([expected,manual],"no"))[0]
    assert row["realized_pnl_dollars"] is None
    assert row["pnl_status"]=="MIXED"


def test_execution_quality_uses_same_v2_down_fill_semantics():
    quote={
        "quoted_unit_price_dollars":"0.8000",
        "quoted_count_fp":"0.11",
        "quoted_fee_dollars":"0.002000",
    }
    stage=history(quote=quote)[0]["stages"][0]
    report=measure_execution_quality([stage],[v2_fill()],TICKER)
    assert report["status"]=="VERIFIED_QUOTE_TO_FILL"
    assert report["stages_with_comparable_quotes"]==1
    assert report["total_adverse_execution_dollars"]=="0.000000"


def test_execution_quality_conflicting_legacy_and_new_identity_still_unavailable():
    quote={
        "quoted_unit_price_dollars":"0.8000",
        "quoted_count_fp":"0.11",
        "quoted_fee_dollars":"0.002000",
    }
    stage=history(quote=quote)[0]["stages"][0]
    report=measure_execution_quality([stage],[
        v2_fill(side="yes",action="buy")
    ],TICKER)
    assert report["status"]=="UNAVAILABLE"
