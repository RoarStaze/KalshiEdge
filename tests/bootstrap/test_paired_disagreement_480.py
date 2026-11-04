"""Paired research-only 480s overlap, including all errors and actual SKIPs."""
from __future__ import annotations

import pytest


def evidence():
    # 0-1: both qualify / agree, one right and one wrong.
    # 2-3: both qualify / disagree, each wins exactly one.
    # 4: logistic only correct.
    # 5-6: nonlinear only, one correct and one wrong.
    # 7: neither qualifies.
    labels=[1,0,1,0,1,0,1,0]
    logistic=[.95,.95,.92,.91,.90,.61,.55,.60]
    nonlinear=[.96,.94,.07,.08,.65,.08,.10,.40]
    return labels,logistic,nonlinear


def test_all_markets_exhaustive_mutually_exclusive_and_properly_counted():
    from kalshi_edge.bootstrap.paired_disagreement_480 import paired_disagreement_480
    y,b,c=evidence()
    r=paired_disagreement_480(y,b,c)
    categories=r["categories"]
    assert r["markets"]==8
    assert {k:v["markets"] for k,v in categories.items()}=={
        "both_qualified_agree":2,
        "both_qualified_disagree":2,
        "logistic_only":1,
        "nonlinear_only":2,
        "neither_qualified":1,
    }
    assert sum(v["markets"] for v in categories.values())==8
    assert categories["both_qualified_agree"]["logistic_correct"]==1
    assert categories["both_qualified_agree"]["nonlinear_incorrect"]==1
    assert categories["both_qualified_disagree"]["logistic_correct"]==1
    assert categories["both_qualified_disagree"]["nonlinear_correct"]==1
    assert categories["both_qualified_disagree"]["logistic_incorrect"]==1
    assert categories["both_qualified_disagree"]["nonlinear_incorrect"]==1
    assert categories["logistic_only"]["nonlinear_issued"]==0
    assert categories["nonlinear_only"]["logistic_issued"]==0
    assert categories["neither_qualified"]["logistic_correct"]==0
    assert categories["neither_qualified"]["nonlinear_correct"]==0
    assert r["baseline_logistic"]["qualified"]==5
    assert r["baseline_logistic"]["correct"]==3
    assert r["baseline_logistic"]["incorrect"]==2
    assert r["candidate_nonlinear"]["qualified"]==6
    assert r["candidate_nonlinear"]["correct"]==3
    assert r["candidate_nonlinear"]["incorrect"]==3
    assert r["net_qualifying_change"]==1
    assert r["net_correct_change"]==0
    assert r["net_incorrect_change"]==1
    assert r["ready_to_promote"] is False
    assert r["independent_validation"] is False


def test_crosswalk_reconciles_net_changes_and_fixed_incumbent_priority():
    from kalshi_edge.bootstrap.paired_disagreement_480 import paired_disagreement_480
    r=paired_disagreement_480(*evidence())
    x=r["change_decomposition"]
    assert x=={
        "nonlinear_only_correct":1,
        "nonlinear_only_incorrect":1,
        "logistic_only_correct":1,
        "logistic_only_incorrect":0,
        "both_disagree_nonlinear_correct":1,
        "both_disagree_logistic_correct":1,
    }
    union=r["logistic_preferred_union"]
    assert union["qualified"]==7
    assert union["correct"]==4
    assert union["incorrect"]==3
    assert union["coverage"]==pytest.approx(7/8)
    assert union["accuracy"]==pytest.approx(4/7)
    assert r["net_correct_change"]==(
        x["nonlinear_only_correct"]-x["logistic_only_correct"]
        +x["both_disagree_nonlinear_correct"]
        -x["both_disagree_logistic_correct"]
    )
    assert r["net_incorrect_change"]==(
        x["nonlinear_only_incorrect"]-x["logistic_only_incorrect"]
        +x["both_disagree_logistic_correct"]
        -x["both_disagree_nonlinear_correct"]
    )


def test_gate_threshold_boundary_and_empty_confidence_inputs():
    from kalshi_edge.bootstrap.paired_disagreement_480 import paired_disagreement_480
    r=paired_disagreement_480([1,0,1,1],[.845,.155,.844999,.55],
                              [.60,.70,.60,.84])
    assert r["baseline_logistic"]["qualified"]==2
    assert r["candidate_nonlinear"]["qualified"]==0
    assert r["categories"]["logistic_only"]["markets"]==2
    assert r["categories"]["neither_qualified"]["markets"]==2


@pytest.mark.parametrize("bad", [
    ([1,0],[.8],[.8,.8]),
    ([1,0],[.9,.8],[.9,float("nan")]),
    ([1,2],[.9,.8],[.9,.8]),
    ([1,0],[.9,1.1],[.9,.8]),
    ([],[],[]),
])
def test_invalid_paired_evidence_fails_closed(bad):
    from kalshi_edge.bootstrap.paired_disagreement_480 import paired_disagreement_480
    with pytest.raises(ValueError):
        paired_disagreement_480(*bad)


def test_rejected_nonlinear_development_still_reports_both_paired_cohorts():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import evaluate_nonlinear_direction
    from kalshi_edge.bootstrap.types import FeatureRow

    ns=1_000_000_000
    def record(i):
        ts=1_900_000_000*ns+i*900*ns
        up=i%2==0
        outcome=int(up if i%17 else not up)
        mid=.73 if up else .27
        features={
            "seconds_remaining":480.,
            "btc_normalized_distance":2. if up else -2.,
            "btc_realized_vol_60s":.0003+(i%7)*.00002,
            "btc_realized_vol_300s":.0005+(i%9)*.00002,
            "btc_return_60s":(.001 if up else -.001),
            "btc_return_60s_available":1.,
            "kalshi_quote_available":1.,
            "kalshi_yes_bid":mid-.025,
            "kalshi_yes_ask":mid+.025,
            "kalshi_mid":mid,
            "kalshi_spread":.05,
            "kalshi_quote_staleness_seconds":12.,
        }
        ticker=f"KXBTC15M-PAIRED-{i}"
        return FeatureRow(
            market_ticker=ticker,split_group_id=ticker,
            checkpoint_ts_ns=ts,label_yes=outcome,features=features,
            source_max_ts_ns={"binance":ts-ns,"kalshi_candles":ts-2*ns},
        )
    rows=[record(i) for i in range(750)]
    development,calibration=rows[:650],rows[650:]
    result=evaluate_nonlinear_direction(development,calibration)
    for key in ("development_screen","development_confirm"):
        report=result[key]["paired_disagreement"]
        assert report["markets"]==result[key]["markets"]
        assert report["baseline_logistic"]["qualified"]==(
            result[key]["baseline_logistic"]["gate_69"]["qualified"]
        )
        assert report["candidate_nonlinear"]["correct"]==(
            result[key]["candidate_nonlinear"]["gate_69"]["correct"]
        )
        assert report["ready_to_promote"] is False
    if result["status"]=="DEVELOPMENT_REJECTED":
        assert result["historical_calibration"] is None
    assert result["safety"]["live_trading_changed"] is False
