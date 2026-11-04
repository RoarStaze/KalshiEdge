"""The enriched BTC 480s challenger must be causally paired and read-only."""
from __future__ import annotations

import pytest

from kalshi_edge.bootstrap.types import FeatureRow

NS=1_000_000_000
TS0=1_900_000_000*NS


def sample_row(i:int, *, checkpoint:float=480.,
               ticker:str|None=None, extra:dict|None=None,
               label:int|None=None) -> FeatureRow:
    up=i%2==0
    y=int(up ^ (i%13 in (0,1))) if label is None else label
    mid=(.73 if up else .27) if i%11 else (.29 if up else .71)
    ts=TS0+i*900*NS
    features={
        "seconds_remaining":checkpoint,
        "btc_normalized_distance":2. if up else -2.,
        "btc_realized_vol_60s":.00025+(i%7)*.00002,
        "btc_realized_vol_300s":.0004+(i%9)*.00002,
        "btc_return_60s":(.0006 if up else -.0006)*(1+i%4),
        "btc_return_60s_available":1.,
        "kalshi_quote_available":1.,
        "kalshi_yes_bid":mid-.02,
        "kalshi_yes_ask":mid+.02,
        "kalshi_mid":mid,
        "kalshi_spread":.04,
        "kalshi_quote_staleness_seconds":10.,
        "btc_return_15s":(.00015 if up else -.00015)*(1+i%5),
        "btc_return_15s_available":1.,
        "btc_taker_buy_imbalance_60s":.15 if up else -.15,
        "btc_market_high_excursion_bps":5.+(i%4),
        "btc_market_low_excursion_bps":-6.-(i%4),
    }
    features.update(extra or {})
    name=ticker or f"KXBTC15M-ENRICH-{i}"
    return FeatureRow(
        market_ticker=name,split_group_id=name,checkpoint_ts_ns=ts,
        label_yes=y,features=features,
        source_max_ts_ns={"binance":ts-NS,"kalshi_candles":ts-2*NS},
    )


def inputs():
    return (
        [sample_row(i) for i in range(650)],
        [sample_row(i) for i in range(650,730)],
    )


def test_fixed_four_additional_features_and_not_searching_gate():
    from kalshi_edge.bootstrap.enriched_direction_480 import (
        EXTRA_FEATURES, ENRICHED_FEATURES,
    )
    from kalshi_edge.bootstrap.specialist_480 import FEATURES
    assert EXTRA_FEATURES==(
        "btc_return_15s",
        "btc_taker_buy_imbalance_60s",
        "btc_market_high_excursion_bps",
        "btc_market_low_excursion_bps",
    )
    assert ENRICHED_FEATURES==FEATURES+EXTRA_FEATURES
    assert len(ENRICHED_FEATURES)==10


def test_missing_or_impossible_new_information_is_excluded_not_filled():
    from kalshi_edge.bootstrap.enriched_direction_480 import enriched_feature_reason
    assert enriched_feature_reason(sample_row(0)) is None
    assert enriched_feature_reason(sample_row(1,extra={
        "btc_return_15s_available":0.
    }))=="missing_15s_return"
    assert enriched_feature_reason(sample_row(1,extra={
        "btc_taker_buy_imbalance_60s":1.1
    }))=="invalid_btc_order_flow"
    assert enriched_feature_reason(sample_row(1,extra={
        "btc_market_high_excursion_bps":-99.
    }))=="invalid_btc_excursion"
    assert enriched_feature_reason(sample_row(1,extra={
        "btc_return_15s":float("nan")
    }))=="missing_or_nonfinite_enriched_feature"


def test_three_models_use_same_eligible_rows_and_no_trading():
    from kalshi_edge.bootstrap.enriched_direction_480 import evaluate_enriched_direction
    dev,cal=inputs()
    r=evaluate_enriched_direction(dev,cal)
    assert r["eligible_development_markets"]==650
    assert r["fit_markets"]==390
    assert r["screen_markets"]==130
    assert r["confirm_markets"]==130
    assert r["eligible_historical_calibration_markets"]==80
    assert r["feature_count_baselines"]==6
    assert r["feature_count_challenger"]==10
    assert r["threshold_changed"] is False
    assert r["model_search_performed"] is False
    assert r["safety"]["ready_to_promote"] is False
    assert r["safety"]["live_trading_changed"] is False
    assert r["safety"]["reserved_lockbox_opened"] is False
    assert r["fit_max_ns"]+900*NS<=r["screen_min_ns"]
    assert r["screen_max_ns"]+900*NS<=r["confirm_min_ns"]
    assert r["confirm_max_ns"]+900*NS<=r["calibration_min_ns"]
    for name in ("screen","confirm"):
        w=r[name]
        assert w["markets"]==130
        for k in ("logistic_6","nonlinear_6","nonlinear_enriched_10"):
            gate=w[k]["gate_69"]
            assert gate["markets"]==130
            assert gate["qualified"]+gate["skipped"]==130
            assert gate["correct"]+gate["incorrect"]==gate["qualified"]
            assert w[k]["probability_metrics"]["brier"]>=0
        assert isinstance(w["joint_objective"],bool)
    if r["status"]=="DEVELOPMENT_REJECTED":
        assert r["historical_calibration"] is None
    else:
        assert r["status"]=="RESEARCH_CALIBRATION_ONLY"


def test_calibration_outcomes_do_not_select_enriched_candidate():
    from kalshi_edge.bootstrap.enriched_direction_480 import evaluate_enriched_direction
    dev,cal=inputs()
    first=evaluate_enriched_direction(dev,cal)
    changed=[r.model_copy(update={"label_yes":1-r.label_yes}) for r in cal]
    second=evaluate_enriched_direction(dev,changed)
    assert first["status"]==second["status"]
    assert first["screen"]==second["screen"]
    assert first["confirm"]==second["confirm"]


def test_wrong_time_duplicates_and_bad_extra_are_excluded_with_reasons():
    from kalshi_edge.bootstrap.enriched_direction_480 import evaluate_enriched_direction
    dev,cal=inputs()
    r=evaluate_enriched_direction(
        [*dev,dev[10],
         sample_row(800,checkpoint=240.),
         sample_row(801,extra={"btc_return_15s_available":0.})],
        cal,
    )
    assert r["eligible_development_markets"]==649
    assert r["development_exclusions"]["duplicate_rows"]==2
    assert r["development_exclusions"]["wrong_checkpoint"]==1
    assert r["development_exclusions"]["missing_15s_return"]==1


def test_reversed_and_overlap_fail_without_scoring():
    from kalshi_edge.bootstrap.enriched_direction_480 import evaluate_enriched_direction
    dev,cal=inputs()
    assert evaluate_enriched_direction(cal,dev)["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_enriched_direction(dev,[dev[-1],*cal])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_enriched_direction(dev[:100],cal)["status"]=="INSUFFICIENT_DEVELOPMENT"
    assert evaluate_enriched_direction(dev,cal[:10])["status"]=="INSUFFICIENT_CALIBRATION"
    reused=cal[0].model_copy(update={
        "market_ticker":dev[0].market_ticker,
        "split_group_id":dev[0].split_group_id,
    })
    assert evaluate_enriched_direction(dev,[reused,*cal[1:]])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"


def test_one_class_fitting_target_refused():
    from kalshi_edge.bootstrap.enriched_direction_480 import evaluate_enriched_direction
    dev,cal=inputs()
    all_yes=[row.model_copy(update={"label_yes":1}) for row in dev]
    assert evaluate_enriched_direction(all_yes,cal)["status"]=="SINGLE_CLASS_TRAIN"
