"""Fixed nonlinear 480s outcome challenger; no threshold or calibration hindsight."""
from __future__ import annotations

import math
import pytest

from kalshi_edge.bootstrap.types import FeatureRow

NS=1_000_000_000
BASE=1_900_000_000*NS


def observation(i:int, *, seconds:float=480., ticker:str|None=None,
                features:dict|None=None, source:dict|None=None,
                label:int|None=None) -> FeatureRow:
    ts=BASE+i*900*NS
    up=(i//3)%2==0
    outcome=(int(up) ^ int(i%13 in (0,1))) if label is None else label
    midpoint=(.73 if up else .27)
    if i%11==0:
        midpoint=1-midpoint
    vals={
        "seconds_remaining":seconds,
        "btc_normalized_distance":1.5 if up else -1.5,
        "btc_realized_vol_60s":.0003+(i%7)*.00003,
        "btc_realized_vol_300s":.0005+(i%13)*.00002,
        "btc_return_60s":(.0006 if up else -.0006)*(1+i%5),
        "btc_return_60s_available":1.,
        "kalshi_quote_available":1.,
        "kalshi_yes_bid":midpoint-.02,
        "kalshi_yes_ask":midpoint+.02,
        "kalshi_mid":midpoint,
        "kalshi_spread":.04,
        "kalshi_quote_staleness_seconds":15.,
    }
    vals.update(features or {})
    name=ticker or f"KXBTC15M-NONLINEAR-{i}"
    return FeatureRow(
        market_ticker=name,split_group_id=name,checkpoint_ts_ns=ts,
        label_yes=outcome,features=vals,
        source_max_ts_ns=(
            {"binance":ts-NS,"kalshi_candles":ts-2*NS}
            if source is None else source
        ),
    )


def cohorts():
    return [observation(i) for i in range(1000)], [
        observation(i) for i in range(1000,1140)]


def test_deterministic_challenger_is_fixed_capacity_not_grid_search():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import _nonlinear_model
    model=_nonlinear_model()
    assert model.learning_rate==.05
    assert model.max_iter==140
    assert model.max_leaf_nodes==9
    assert model.min_samples_leaf==80
    assert model.l2_regularization==5.
    assert model.early_stopping is False
    assert model.random_state==0


def test_development_only_time_ordered_comparison_and_no_live_mutation():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import evaluate_nonlinear_direction
    train,valid=cohorts()
    result=evaluate_nonlinear_direction(train,valid)
    assert result["fit_markets"]==600
    assert result["development_screen_markets"]==200
    assert result["development_confirm_markets"]==200
    assert result["historical_calibration_markets"]==140
    assert result["training_max_ns"]+900*NS<=result["screen_min_ns"]
    assert result["screen_max_ns"]+900*NS<=result["confirm_min_ns"]
    assert result["confirm_max_ns"]+900*NS<=result["calibration_min_ns"]
    assert result["model_search_performed"] is False
    assert result["threshold_changed"] is False
    assert result["safety"]["ready_to_promote"] is False
    assert result["safety"]["model_saved"] is False
    assert result["safety"]["live_trading_changed"] is False
    assert result["safety"]["reserved_lockbox_opened"] is False
    for period in ("development_screen","development_confirm"):
        metrics=result[period]
        for model in ("baseline_logistic","candidate_nonlinear"):
            gate=metrics[model]["gate_69"]
            assert gate["qualified"]+gate["skipped"]==metrics["markets"]
            assert gate["correct"]+gate["incorrect"]==gate["qualified"]
            assert 0<=metrics[model]["probability_metrics"]["brier"]<=1
        assert isinstance(metrics["joint_objective_met"],bool)
    if result["status"]=="DEVELOPMENT_REJECTED":
        assert result["historical_calibration"] is None
    else:
        assert result["status"]=="RESEARCH_CALIBRATION_ONLY"


def test_calibration_outcomes_never_influence_earlier_screen_confirmation():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import evaluate_nonlinear_direction
    a,b=cohorts()
    first=evaluate_nonlinear_direction(a,b)
    flipped=[r.model_copy(update={"label_yes":1-r.label_yes}) for r in b]
    second=evaluate_nonlinear_direction(a,flipped)
    assert first["development_screen"]==second["development_screen"]
    assert first["development_confirm"]==second["development_confirm"]
    assert first["status"]==second["status"]
    assert first["training_max_ns"]==second["training_max_ns"]


def test_fails_closed_for_overlap_reversal_and_insufficient_sample():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import evaluate_nonlinear_direction
    a,b=cohorts()
    assert evaluate_nonlinear_direction(b,a)["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_nonlinear_direction(a,[a[-1],*b])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_nonlinear_direction(a[:199],b)["status"]=="INSUFFICIENT_DEVELOPMENT"
    assert evaluate_nonlinear_direction(a,b[:19])["status"]=="INSUFFICIENT_CALIBRATION"
    other=b[0].model_copy(update={
        "market_ticker":a[0].market_ticker,
        "split_group_id":a[0].split_group_id,
    })
    assert evaluate_nonlinear_direction(a,[other,*b[1:]])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"


def test_240s_and_unhealthy_quotes_and_duplicate_excluded():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import evaluate_nonlinear_direction
    a,b=cohorts()
    bad_quote=observation(1300,features={"kalshi_quote_available":0.})
    bad_time=observation(1301,seconds=240.)
    duplicated=a[50]
    report=evaluate_nonlinear_direction(
        [*a,duplicated,bad_quote,bad_time],b)
    assert report["development_exclusions"]["duplicate_rows"]==2
    assert report["development_exclusions"]["wrong_checkpoint"]==1
    assert report["development_exclusions"]["invalid_or_stale_quote"]==1
    assert report["fit_markets"]==599*60//100


def test_single_class_training_refuses_even_when_later_rows_have_both_labels():
    from kalshi_edge.bootstrap.nonlinear_direction_480 import evaluate_nonlinear_direction
    a,b=cohorts()
    all_yes=[r.model_copy(update={"label_yes":1}) for r in a]
    assert evaluate_nonlinear_direction(all_yes,b)["status"]=="SINGLE_CLASS_TRAIN"
