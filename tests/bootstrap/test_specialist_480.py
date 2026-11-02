"""Train a genuine 480s specialist on causal feature rows, not reused 1Hz replay."""
from __future__ import annotations

import math
import pytest

from kalshi_edge.bootstrap.types import FeatureRow
from kalshi_edge.bootstrap.specialist_480 import (
    select_eight_minute_rows, evaluate_eight_minute_specialist,
    MIN_TRAIN_MARKETS, MIN_VALIDATION_MARKETS,
)

NS=1_000_000_000
BASE=1_900_000_000*NS


def make_row(i:int, *, positive=None, seconds=480.0, ticker=None, feature_overrides=None, source_override=None):
    yes = (i % 2 == 0) if positive is None else positive
    ts=BASE + i*900*NS
    price=.73 if yes else .27
    f={
        "seconds_remaining": seconds,
        "btc_normalized_distance":3.0 if yes else -3.0,
        "btc_realized_vol_60s":.0003,
        "btc_realized_vol_300s":.0004,
        "btc_return_60s":.001 if yes else -.001,
        "btc_return_60s_available":1.0,
        "kalshi_quote_available":1.0,
        "kalshi_yes_bid":price-.025,
        "kalshi_yes_ask":price+.025,
        "kalshi_mid":price,
        "kalshi_spread":.05,
        "kalshi_quote_staleness_seconds":35.0,
    }
    if feature_overrides:
        f.update(feature_overrides)
    return FeatureRow(
        market_ticker=ticker or f"KXBTC15M-SPEC-{i}",
        split_group_id=ticker or f"KXBTC15M-SPEC-{i}",
        checkpoint_ts_ns=ts,
        label_yes=int(yes),
        features=f,
        source_max_ts_ns={"binance":ts-NS,"kalshi_candles":ts-2*NS}
        if source_override is None else source_override,
    )


def cohorts():
    return [make_row(i) for i in range(100)], [make_row(i) for i in range(100,140)]


def test_trains_actual_480s_specialist_with_out_of_fit_later_market_report():
    train,valid=cohorts()
    result=evaluate_eight_minute_specialist(train,valid)
    assert result["status"]=="RESEARCH_VALIDATION_ONLY"
    assert result["fit_markets"]==100
    assert result["validation_markets"]==40
    assert result["last_fit_ticker"]==train[-1].market_ticker
    assert result["first_validation_ticker"]==valid[0].market_ticker
    assert result["candidate"]["brier"] < result["kalshi_midpoint"]["brier"]
    assert result["candidate"]["log_loss"] < result["kalshi_midpoint"]["log_loss"]
    assert result["candidate"]["accuracy"] >= .95
    assert result["candidate_69_gate"]["eligible_markets"] > 0
    assert result["candidate_69_gate"]["correct"] > 0
    assert result["candidate_69_gate"]["coverage"] > 0
    paired = result["candidate_vs_kalshi_69_overlap"]
    assert paired["total_markets"] == 40
    assert (paired["both_qualified"]["markets"] + paired["candidate_only"]["markets"]
            + paired["baseline_only"]["markets"] + paired["neither_qualified"]) == 40
    assert paired["net_new_qualifying_forecasts"] == (
        result["candidate_69_gate"]["eligible_markets"]
        - result["kalshi_69_gate"]["eligible_markets"]
    )
    assert result["joint_objective_against_kalshi_retrospectively"] is False
    assert result["qualified_69_gate_verified_profit"] is None
    assert result["model_saved"] is False
    assert result["can_promote"] is False
    assert result["independent_lockbox"] is False


def test_only_true_eight_minute_rows_selected_and_no_240_contamination():
    rows=[
        make_row(1,seconds=240),
        make_row(2,seconds=480),
        make_row(3,seconds=479.1),
        make_row(4,seconds=478.7),
    ]
    selected,excluded=select_eight_minute_rows(rows)
    assert [r.market_ticker for r in selected]==[
        "KXBTC15M-SPEC-2","KXBTC15M-SPEC-3",
    ]
    assert excluded["wrong_checkpoint"]==2


def test_invalid_quote_or_unavailable_required_momentum_fail_closed():
    cases=[
        {"kalshi_quote_available":0.0},
        {"kalshi_yes_ask":.10,"kalshi_yes_bid":.90},
        {"kalshi_yes_ask":0},
        {"kalshi_mid":0},
        {"kalshi_quote_staleness_seconds":140.0},
        {"btc_return_60s_available":0.0},
        {"btc_realized_vol_60s":0.0},
        {"btc_realized_vol_300s":math.nan},
        {"btc_normalized_distance":math.inf},
    ]
    for i,features in enumerate(cases):
        selected,_=select_eight_minute_rows([make_row(i,feature_overrides=features)])
        assert selected==[],features


def test_future_source_times_and_missing_source_provenance_excluded():
    r1=make_row(1,source_override={"binance":BASE+900*NS+NS})
    r2=make_row(2,source_override={})
    rows,reasons=select_eight_minute_rows([r1,r2])
    assert not rows
    assert reasons["future_or_missing_source"]==2


def test_duplicate_market_rows_are_both_removed_not_cherry_picked():
    original=make_row(1)
    duplicate=make_row(2,ticker=original.market_ticker)
    selected,reason=select_eight_minute_rows([original,duplicate,make_row(3)])
    assert [x.market_ticker for x in selected]==["KXBTC15M-SPEC-3"]
    assert reason["duplicate_rows"]==2


def test_minimum_market_requirements_return_explicit_unknown():
    train,val=cohorts()
    assert evaluate_eight_minute_specialist(train[:79],val)["status"]=="INSUFFICIENT_TRAIN_MARKETS"
    assert evaluate_eight_minute_specialist(train,val[:19])["status"]=="INSUFFICIENT_VALIDATION_MARKETS"


def test_invalid_chronology_and_market_group_overlap_rejected():
    train,val=cohorts()
    assert evaluate_eight_minute_specialist(val,train)["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_eight_minute_specialist(train,[train[10],*val])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"


def test_one_class_train_returns_no_model_or_score():
    train,val=cohorts()
    all_above=[make_row(i,positive=True) for i in range(100)]
    result=evaluate_eight_minute_specialist(all_above,val)
    assert result["status"]=="SINGLE_CLASS_TRAIN_SET"
    assert result["candidate"] is None


def test_extra_checkpoints_same_market_do_not_remove_valid_480_record():
    # Filter by checkpoint first; a 240s row of the same market is normal
    # and must NOT be treated as a duplicate 480s sample.
    base=make_row(1)
    old=base.model_copy(update={"features":{**base.features,"seconds_remaining":240.0}})
    selected,_=select_eight_minute_rows([base,old])
    assert len(selected)==1


def test_original_kalshi_midpoint_benchmark_uses_identical_validation_rows():
    train,valid=cohorts()
    result=evaluate_eight_minute_specialist(train,valid)
    assert result["candidate"]["markets"]==result["kalshi_midpoint"]["markets"]==40
    assert result["fit_last_ns"] < result["eval_first_ns"]
