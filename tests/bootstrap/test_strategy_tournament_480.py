"""Real KXBTC15M candidate comparison cannot be promoted by synthetic backtests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from kalshi_edge.bootstrap.strategy_tournament_480 import (
    Case, candidate_probability, evaluate_tournament, read_verified_cases,
)

NS=1_000_000_000
START=1_900_000_000*NS


def _case(i:int, *, outcome:int|None=None, fetched_delay:int=550) -> Case:
    # Distances alternate but retain a non-zero estimated 1s volatility.
    positive=(i%4)<2
    features={
        "btc_normalized_distance": 2.2 if positive else -2.2,
        "btc_return_60s": .00055 if positive else -.00055,
        "btc_return_300s": .00090 if positive else -.00090,
        "btc_realized_vol_60s": .00008,
        "btc_return_60s_available": 1.0,
        "btc_return_300s_available": 1.0,
    }
    ts=START+i*900*NS
    return Case(
        ticker=f"KXBTC15M-LAB-{i:05d}-00",
        checkpoint_ts_ns=ts,
        settlement_fetched_ts_ns=ts+fetched_delay*NS,
        label_yes=int(positive) if outcome is None else outcome,
        incumbent_p_yes=.8 if i%3 else .2,
        coinflip_high=False,
        features=features,
    )


def test_indicators_use_only_checkpoint_feature_values():
    features=_case(0).features
    for name in ("strike_distance","trend_confirmation","pullback","mean_reversion"):
        p=candidate_probability(name,features)
        assert isinstance(p,float)
        assert 0<p<1
    assert candidate_probability("trend_confirmation",{
        **features,"btc_return_60s_available":0.0,
    }) is None
    assert candidate_probability("strike_distance",{
        **features,"btc_realized_vol_60s":float("nan"),
    }) is None
    with pytest.raises(ValueError):
        candidate_probability("does-not-exist",features)


def test_small_sample_refuses_strategy_promotion():
    report=evaluate_tournament([_case(i) for i in range(15)],min_total=60,min_days=1)
    assert report["status"]=="INSUFFICIENT_VERIFIED_MARKETS"
    assert report["ready_to_promote"] is False
    assert report["live_forecasts_changed"] is False
    assert report["autotrade_changed"] is False
    assert report["verified_net_profit"] is False


def test_holdout_labels_never_select_candidate():
    baseline=[_case(i) for i in range(90)]
    first=evaluate_tournament(baseline,min_total=60,min_days=1)
    altered=[
        replace(item,label_yes=1-item.label_yes) if i>=72 else item
        for i,item in enumerate(baseline)
    ]
    second=evaluate_tournament(altered,min_total=60,min_days=1)
    assert first["selection"]["candidate"] == second["selection"]["candidate"]
    assert first["selection"]["validation_scores"] == second["selection"]["validation_scores"]
    assert first["counts"]["test_markets"]==second["counts"]["test_markets"]
    assert first["status"]!="PROVEN_PROFITABLE"


def test_training_outcomes_fetched_after_validation_start_do_not_fit_selection():
    cases=[_case(i) for i in range(90)]
    # n=90 -> 54 train, 18 validate, 18 holdout
    cases[0]=replace(cases[0],settlement_fetched_ts_ns=cases[54].checkpoint_ts_ns+NS)
    cases[60]=replace(cases[60],settlement_fetched_ts_ns=cases[72].checkpoint_ts_ns+NS)
    result=evaluate_tournament(cases,min_total=60,min_days=1)
    assert result["counts"]["training_available_by_validation_start"]==53
    assert result["counts"]["validation_available_by_test_start"]==17
    assert result["counts"]["test_markets"]==18


def test_no_cherry_picking_market_subset_to_make_winner_look_better():
    cases=[_case(i) for i in range(90)]
    result=evaluate_tournament(cases,min_total=60,min_days=1)
    for name,score in result["validation"].items():
        assert score["total"]==result["counts"]["validation_available_by_test_start"]
        assert score["qualified"]<=score["total"]
    for name,score in result["test"].items():
        assert score["total"]==result["counts"]["test_markets"]
    assert result["promotion_review"]["automatic_promotion"] is False
    assert result["data_source"]=="OFFICIAL_SETTLEMENT_AND_IMMUTABLE_CHECKPOINT_SNAPSHOT"


def test_tampered_or_unsettled_snapshot_never_enters_backtest(tmp_path):
    from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord,FinalForecastWriter
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter,build_forward_snapshot,
    )
    from kalshi_edge.bootstrap.final_history import OfficialSettlement,_save_settlement
    from kalshi_edge.bootstrap.types import FeatureRow
    ts=START
    ticker="KXBTC15M-LAB-00000-00"
    final=FinalForecastRecord(
        market_ticker=ticker,strike=100000.,scheduled_ts_ns=ts,
        prediction_ts_ns=ts,seconds_remaining=480.,
        checkpoint_seconds=480,forecast="ABOVE",
        p_yes=.8,p_no=.2,confidence=.6,model_hash="a"*64,
    )
    saved=FinalForecastWriter(tmp_path).save(final,market_close_ts_ns=ts+480*NS)
    assert saved
    row=FeatureRow(
        market_ticker=ticker,checkpoint_ts_ns=ts,
        label_yes=1,  # dummy live label MUST NOT enter research
        features=_case(0).features,
        source_max_ts_ns={"binance":ts-NS},
    )
    snap=build_forward_snapshot(final,saved,row)
    path=Forward480FeatureWriter(tmp_path).save(snap)
    assert path
    assert read_verified_cases(tmp_path)==[]
    _save_settlement(tmp_path,final,OfficialSettlement(
        market_ticker=ticker,market_close_ts_ns=ts+480*NS,
        result="no",actual_side="BELOW",
        fetched_ts_ns=ts+550*NS,market_status="settled",
    ))
    cases=read_verified_cases(tmp_path)
    assert len(cases)==1
    assert cases[0].label_yes==0
    path.write_text(path.read_text().replace('"btc_return_60s":','"btc_return_60s":9999, "garbage":'))
    assert read_verified_cases(tmp_path)==[]
