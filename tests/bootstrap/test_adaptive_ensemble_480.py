"""Tests for the prospective, causally fitted 480s settlement ensemble."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.settlement_challenger_480 import (
    SettlementShadowWriter, build_settlement_shadow,
)
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.bootstrap.adaptive_ensemble_480 import (
    AdaptiveEnsembleWriter, audit_adaptive_ensemble, build_adaptive_ensemble,
    load_adaptive_ensemble, prior_training_pairs, select_blend,
)

NS = 1_000_000_000
START = 1_900_000_000 * NS


def _pair(root: Path, index: int, *, actual: str | None = "ABOVE",
          fetched_offset_s: int = 600, final_p: float = .85):
    ts = START + index * 900 * NS
    final = FinalForecastRecord(
        market_ticker=f"KXBTC15M-ENSEMBLE-{index:05d}-00",
        strike=100000.0, scheduled_ts_ns=ts, prediction_ts_ns=ts,
        checkpoint_seconds=480, seconds_remaining=480.0,
        forecast="ABOVE" if final_p >= .5 else "BELOW",
        confidence=2 * abs(final_p-.5), p_yes=final_p, p_no=1-final_p,
        model_hash="a"*64,
    )
    final_file = FinalForecastWriter(root).save(final, market_close_ts_ns=ts+480*NS)
    assert final_file is not None
    prediction = LivePredictionRecord(
        market_ticker=final.market_ticker,prediction_ts_ns=ts,
        status="OK",p_yes=final_p,p_no=1-final_p,
        predicted_side="ABOVE" if final_p >= .5 else "BELOW",
        feed_quality=FeedQuality(healthy=True), model_hash="a"*64,
        brti_value=100125 if index % 2 else 99875, brti_source_ts_ns=ts-NS,
        brti_shadow_risk_sigma=.00013,
        brti_shadow_risk_observed_seconds=90.0,
        brti_shadow_risk_observation_count=92,
    )
    research=build_settlement_shadow(final,prediction,final_file)
    assert SettlementShadowWriter(root).save(research) is not None
    if actual is not None:
        _save_settlement(root,final,OfficialSettlement(
            market_ticker=final.market_ticker,
            market_close_ts_ns=ts+480*NS,
            result="yes" if actual=="ABOVE" else "no",
            actual_side=actual, fetched_ts_ns=ts+fetched_offset_s*NS,
            market_status="settled",
        ))
    return final, research


def test_training_uses_only_outcomes_fetched_before_prediction(tmp_path):
    for index in range(84):
        _pair(tmp_path,index,actual="ABOVE" if index % 2 else "BELOW")
    # Fetched after 84 is predicted, so excluded; current market excluded.
    current,_=_pair(tmp_path,84,actual="BELOW",fetched_offset_s=600)
    prior=prior_training_pairs(tmp_path,current)
    assert len(prior)==84
    assert all(pair.fetched_ts_ns < current.prediction_ts_ns for pair in prior)
    assert all(pair.ticker != current.market_ticker for pair in prior)
    future,_=_pair(tmp_path,85,actual="ABOVE")
    assert len(prior_training_pairs(tmp_path,current))==84
    assert future.market_ticker not in {x.ticker for x in prior}


def test_blend_selects_deterministic_brier_candidate_and_rejects_nonfinite():
    # Challenger perfectly predicts both outcomes; incumbent predicts opposite.
    observations=[(.1,.95,1),(.9,.05,0)]*40
    assert select_blend(observations)==(1.0,1.0)
    with pytest.raises(ValueError):
        select_blend([(.1,float("nan"),1)])


def test_insufficient_history_records_untrained_not_a_fake_forecast(tmp_path):
    for index in range(4):
        _pair(tmp_path,index)
    final,_=_pair(tmp_path,4,actual=None)
    record=build_adaptive_ensemble(tmp_path,final)
    assert record.status=="UNTRAINED"
    assert record.p_yes is None
    assert record.training_count==4
    saved=AdaptiveEnsembleWriter(tmp_path).save(record)
    assert saved is not None
    assert AdaptiveEnsembleWriter(tmp_path).save(record) is None
    assert load_adaptive_ensemble(tmp_path,final)==record
    assert '"actual_side"' not in saved.read_text()


def test_prospective_ready_record_is_bound_to_both_sources(tmp_path):
    for index in range(80):
        _pair(tmp_path,index,actual="ABOVE" if index % 2 else "BELOW")
    final,_=_pair(tmp_path,80,actual=None)
    record=build_adaptive_ensemble(tmp_path,final)
    assert record.status=="READY"
    assert record.training_count==80
    assert record.p_yes is not None
    assert record.training_sha256
    assert record.challenger_sha256
    saved=AdaptiveEnsembleWriter(tmp_path).save(record)
    assert saved is not None
    assert load_adaptive_ensemble(tmp_path,final)==record
    corrupt=json.loads(saved.read_text())
    corrupt["p_yes"]=.001
    saved.write_text(json.dumps(corrupt))
    with pytest.raises(ValueError,match="hash|integrity"):
        load_adaptive_ensemble(tmp_path,final)


def test_corrupted_prior_research_cannot_enter_fit(tmp_path):
    for index in range(80):
        _pair(tmp_path,index)
    final,_=_pair(tmp_path,80,actual=None)
    prior=prior_training_pairs(tmp_path,final)
    assert len(prior)==80
    shadow=list((tmp_path/"research"/"settlement_challenger_480").glob("*/*00005-00.json"))
    assert len(shadow)==1
    shadow[0].write_text(shadow[0].read_text().replace('"status":"OK"','"status":"UNAVAILABLE"'))
    assert len(prior_training_pairs(tmp_path,final))==79
    assert build_adaptive_ensemble(tmp_path,final).status=="UNTRAINED"


def test_paired_scorecard_only_scores_saved_ready_forecasts(tmp_path):
    for index in range(80):
        _pair(tmp_path,index)
    ready,_=_pair(tmp_path,80,actual="BELOW")
    other,_=_pair(tmp_path,81,actual="ABOVE")
    assert AdaptiveEnsembleWriter(tmp_path).save(build_adaptive_ensemble(tmp_path,ready))
    # Other settled final has no ensemble; must not be backfilled.
    report=audit_adaptive_ensemble(tmp_path)
    assert report["status"]=="RESEARCH_ONLY"
    assert report["counts"]["paired_settled_ready"]==1
    assert report["counts"]["missing_ensemble_settled"]>=1
    assert report["ensemble_metrics"]["brier"] is not None
    assert report["promotion_review"]["eligible"] is False
    assert report["live_model_changed"] is False
    assert report["autotrade_changed"] is False
    assert report["verified_profitability"] is False


def test_capture_in_live_loop_is_after_existing_settlement_shadow():
    import inspect
    from kalshi_edge.bootstrap.live import _run_live_async
    source=inspect.getsource(_run_live_async)
    assert "capture_adaptive_ensemble(" in source
    assert source.index("capture_settlement_shadow(") < source.index("capture_adaptive_ensemble(")
    assert "if saved_path is not None" in source
