"""Causal, frozen-policy tests for 480-second correctness selection research."""
from __future__ import annotations

import math
import pytest
from kalshi_edge.bootstrap.types import FeatureRow

NS=1_000_000_000
BASE=1_900_000_000 * NS


def row(i:int, *, seconds:float=480.0, market:str|None=None, features:dict|None=None):
    ts=BASE+i*900*NS
    up=(i%2==0)
    # Some feature-to-settlement disagreement is essential for
    # checking a correctness classifier instead of a second direction fit.
    y=int(up != (i%11==0 or i%17==0))
    mid=(.74 if up else .26) if i%9 else (.3 if up else .7)
    f={
        "seconds_remaining":seconds,
        "btc_normalized_distance": 1.8 if up else -1.8,
        "btc_realized_vol_60s": .00025 + (i%7)*.00003,
        "btc_realized_vol_300s": .00045 + (i%11)*.00002,
        "btc_return_60s": (.0005 if up else -.0005)*(1+i%5),
        "btc_return_60s_available":1.0,
        "kalshi_quote_available":1.0,
        "kalshi_yes_bid":mid-.015,
        "kalshi_yes_ask":mid+.015,
        "kalshi_mid":mid,
        "kalshi_spread":.03,
        "kalshi_quote_staleness_seconds":3.0 + i%6,
    }
    f.update(features or {})
    name=market or f"KXBTC15M-REL-{i}"
    return FeatureRow(
        market_ticker=name,split_group_id=name,
        checkpoint_ts_ns=ts,label_yes=y,features=f,
        source_max_ts_ns={"binance":ts-NS,"kalshi_candles":ts-2*NS},
    )


def groups():
    return [row(i) for i in range(160)],[row(i) for i in range(160,205)]


def test_fixed_reliability_gate_metrics_do_not_discard_incorrect_forecasts():
    from kalshi_edge.bootstrap.selective_reliability_480 import gate_metrics
    labels=[1,0,1,0,1]
    probabilities=[.9,.9,.6,.2,.3]
    qualified=[True,True,False,True,False]
    g=gate_metrics(labels,probabilities,qualified)
    assert g["markets"]==5 and g["qualified"]==3
    assert g["correct"]==2 and g["incorrect"]==1
    assert g["skipped"]==2
    assert g["coverage"]==pytest.approx(.6)
    assert g["accuracy"]==pytest.approx(2/3)
    assert g["correct_per_market"]==pytest.approx(.4)
    with pytest.raises(ValueError):
        gate_metrics(labels, probabilities, [True]*4)


def test_eight_minute_meta_research_runs_strict_out_of_fold_and_no_promotion():
    from kalshi_edge.bootstrap.selective_reliability_480 import evaluate_reliability_gate
    train, eval_rows=groups()
    r=evaluate_reliability_gate(train,eval_rows)
    assert r["status"]=="RESEARCH_CALIBRATION_ONLY"
    assert r["direction_fit_markets"]==160
    assert r["evaluation_markets"]==45
    assert r["oof_correctness_training_markets"]>=60
    assert r["reliability_targets_correct"]>0
    assert r["reliability_targets_incorrect"]>0
    assert r["training_last_ns"] < r["evaluation_first_ns"]
    assert r["oof_folds"]
    for fold in r["oof_folds"]:
        assert fold["training_max_ns"] + 900*NS <= fold["evaluation_min_ns"]
        assert fold["training_markets"]>=80
        assert fold["oof_markets"]>0
    for name in ("baseline_69", "reliability_85", "rescue_union"):
        gate=r[name]
        assert gate["markets"]==45
        assert gate["correct"]+gate["incorrect"]==gate["qualified"]
        assert gate["qualified"]+gate["skipped"]==45
    assert r["safety"]["independent_holdout"] is False
    assert r["safety"]["ready_to_promote"] is False
    assert r["safety"]["model_saved"] is False
    assert r["safety"]["live_trading_changed"] is False


def test_future_and_overlap_fail_closed_even_if_small_dataset():
    from kalshi_edge.bootstrap.selective_reliability_480 import evaluate_reliability_gate
    a,b=groups()
    assert evaluate_reliability_gate(b,a)["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_reliability_gate(a,[a[-1],*b])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_reliability_gate(a[:79],b)["status"]=="INSUFFICIENT_DIRECTION_TRAINING"
    assert evaluate_reliability_gate(a,b[:19])["status"]=="INSUFFICIENT_EVALUATION"
    assert evaluate_reliability_gate(a,[row(160,market=a[0].market_ticker),*b[1:]])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"


def test_240s_missing_quotes_and_duplicate_market_rows_excluded():
    from kalshi_edge.bootstrap.selective_reliability_480 import evaluate_reliability_gate
    a,b=groups()
    duplicates=[a[3],a[3]]
    wrong=row(310,seconds=240.0)
    bad=row(311,features={"kalshi_quote_available":0.0})
    r=evaluate_reliability_gate([*a,*duplicates,wrong,bad],b)
    assert r["status"]=="RESEARCH_CALIBRATION_ONLY"
    assert r["direction_fit_markets"]==159
    assert r["development_exclusions"]["duplicate_rows"]==3
    assert r["development_exclusions"]["wrong_checkpoint"]==1
    assert r["development_exclusions"]["invalid_or_stale_quote"]==1


def test_no_correctness_target_variation_declines_meta_fit():
    from kalshi_edge.bootstrap.selective_reliability_480 import evaluate_reliability_gate
    a,b=groups()
    # Same-class *direction* target is blocked before reliability fitting.
    all_yes=[r.model_copy(update={"label_yes":1}) for r in a]
    r=evaluate_reliability_gate(all_yes,b)
    assert r["status"]=="SINGLE_CLASS_DIRECTION_TARGET"


def test_reliability_features_are_finite_and_only_checkpoint_evidence():
    from kalshi_edge.bootstrap.selective_reliability_480 import reliability_features
    r=row(1)
    values=reliability_features(r,.76)
    assert len(values)==9
    assert all(math.isfinite(v) for v in values)
    assert reliability_features(r,.76)!=reliability_features(r,.24)
    with pytest.raises(ValueError):
        reliability_features(r,float("nan"))
