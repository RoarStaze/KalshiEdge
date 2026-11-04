"""Prospective 480s feature evidence must be immutable, causal, and label-free."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.types import FeatureRow

NS=1_000_000_000
BASE=1_900_000_000*NS
MODEL="a"*64


def final_at(root:Path, i:int, *, forecast:str="ABOVE",
             checkpoint:int=480, confidence:float=.8):
    scheduled=BASE+i*900*NS
    close=scheduled+checkpoint*NS
    ticker=f"KXBTC15M-FORWARD-{i:04d}-00"
    p=.9 if forecast=="ABOVE" else .1 if forecast=="BELOW" else .7
    if forecast=="SKIP":
        confidence=.4
    r=FinalForecastRecord(
        market_ticker=ticker,strike=100000.,
        scheduled_ts_ns=scheduled,prediction_ts_ns=scheduled,
        seconds_remaining=checkpoint,checkpoint_seconds=checkpoint,
        forecast=forecast,
        reason="CONFIDENCE_BELOW_69_PERCENT" if forecast=="SKIP" else None,
        confidence=confidence,p_yes=p,p_no=1-p,model_hash=MODEL,
    )
    path=FinalForecastWriter(root).save(r,market_close_ts_ns=close)
    assert path is not None
    return r,path


def features(r:FinalForecastRecord):
    return FeatureRow(
        market_ticker=r.market_ticker,
        checkpoint_ts_ns=r.prediction_ts_ns,
        # The live feature builder deliberately supplies a DUMMY label!
        label_yes=0,
        features={
            "seconds_remaining":480.,
            "btc_normalized_distance":1.2,
            "btc_return_15s":.001,
            "btc_taker_buy_imbalance_60s":.23,
            "kalshi_quote_available":1.,
        },
        source_max_ts_ns={
            "binance":r.prediction_ts_ns-NS,
            "kalshi_candles":r.prediction_ts_ns-60*NS,
        },
    )


def settle(root:Path,final:FinalForecastRecord,actual:str):
    return _save_settlement(root,final,OfficialSettlement(
        market_ticker=final.market_ticker,
        market_close_ts_ns=final.scheduled_ts_ns+final.checkpoint_seconds*NS,
        result="yes" if actual=="ABOVE" else "no",
        actual_side=actual,
        source="kalshi-public-market-api",
        market_status="settled",
        fetched_ts_ns=final.scheduled_ts_ns+540*NS,
    ))


def test_snapshot_saved_once_has_no_label_and_binds_exact_final(tmp_path:Path):
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter,build_forward_snapshot,load_forward_snapshot,
    )
    r,path=final_at(tmp_path,1)
    item=build_forward_snapshot(r,path,features(r))
    writer=Forward480FeatureWriter(tmp_path)
    output=writer.save(item)
    assert output is not None
    assert writer.save(item) is None
    raw=json.loads(output.read_text())
    text=output.read_text()
    for forbidden in ('"label_yes"','"actual_side"','"result"','"settlement"','"correct"'):
        assert forbidden not in text
    assert raw["status"]=="FEATURES_OK"
    assert raw["features"]["btc_taker_buy_imbalance_60s"]==pytest.approx(.23)
    assert load_forward_snapshot(tmp_path,r)==item


def test_missing_feature_is_explicit_no_fabricated_label(tmp_path:Path):
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter,build_forward_snapshot,
    )
    r,path=final_at(tmp_path,2,forecast="SKIP")
    item=build_forward_snapshot(r,path,None,reason="UNHEALTHY_CHECKPOINT_INPUTS")
    assert item.status=="FEATURES_UNAVAILABLE"
    assert item.features=={}
    assert item.source_max_ts_ns=={}
    assert item.reason=="UNHEALTHY_CHECKPOINT_INPUTS"
    assert Forward480FeatureWriter(tmp_path).save(item) is not None


def test_bad_source_future_market_or_nonfinite_feature_rejected(tmp_path:Path):
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import build_forward_snapshot
    r,path=final_at(tmp_path,3)
    candidate=features(r)
    with pytest.raises(ValueError):
        build_forward_snapshot(r,path,candidate.model_copy(update={
            "source_max_ts_ns":{"binance":r.prediction_ts_ns+NS},
        }))
    with pytest.raises(ValueError):
        build_forward_snapshot(r,path,candidate.model_copy(update={
            "market_ticker":"KXBTC15M-WRONG",
        }))
    with pytest.raises(ValueError):
        build_forward_snapshot(r,path,candidate.model_copy(update={
            "features":{**candidate.features,"btc_extra":float("nan")},
        }))
    with pytest.raises(ValueError):
        build_forward_snapshot(r,path,candidate.model_copy(update={
            "features":{**candidate.features,"btc_extra":True},
        }))
    with pytest.raises(ValueError):
        build_forward_snapshot(r,path,candidate.model_copy(update={
            "features":{**candidate.features,"label_yes":0},
        }))
    with pytest.raises(ValueError):
        build_forward_snapshot(r,path,candidate.model_copy(update={
            "checkpoint_ts_ns":r.prediction_ts_ns-NS,
        }))


def test_rejects_mismatched_forecast_file_old_checkpoint_and_conflict(tmp_path:Path):
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter,build_forward_snapshot,load_forward_snapshot,
    )
    a,pa=final_at(tmp_path,4)
    b,pb=final_at(tmp_path,5)
    with pytest.raises(ValueError):
        build_forward_snapshot(a,pb,features(a))
    old,old_path=final_at(tmp_path,6,checkpoint=240)
    with pytest.raises(ValueError):
        build_forward_snapshot(old,old_path,None,reason="LEGACY")
    w=Forward480FeatureWriter(tmp_path)
    item=build_forward_snapshot(a,pa,features(a))
    output=w.save(item)
    assert output
    unavailable=build_forward_snapshot(a,pa,None,reason="LATER_FAILURE")
    with pytest.raises(ValueError):
        w.save(unavailable)
    payload=json.loads(output.read_text())
    payload["features"]["btc_normalized_distance"]=99
    output.write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(ValueError):
        load_forward_snapshot(tmp_path,a)


def test_training_labels_only_from_verified_settlement_and_complete_audit(tmp_path:Path):
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter,build_forward_snapshot,
        evaluate_forward_feature_evidence,verified_forward_feature_examples,
    )
    writer=Forward480FeatureWriter(tmp_path)
    a,pa=final_at(tmp_path,10,forecast="ABOVE")
    b,pb=final_at(tmp_path,11,forecast="ABOVE")
    c,pc=final_at(tmp_path,12,forecast="SKIP")
    d,pd=final_at(tmp_path,13,forecast="ABOVE")  # Legacy/missed snapshot
    e,pe=final_at(tmp_path,14,forecast="BELOW") # Not yet settled
    for f,p in ((a,pa),(b,pb),(c,pc),(e,pe)):
        writer.save(build_forward_snapshot(f,p,features(f)))
    settle(tmp_path,a,"ABOVE")
    settle(tmp_path,b,"BELOW")
    settle(tmp_path,c,"BELOW")
    settle(tmp_path,d,"ABOVE")

    s=evaluate_forward_feature_evidence(tmp_path)
    assert s["settled_480s"]==4
    assert s["pending_outcomes"]==1
    assert s["missing_snapshot_settled"]==1
    assert s["verified_feature_examples"]==3
    assert s["issued_correct"]==2
    assert s["issued_incorrect"]==1
    assert s["confidence_skips"]==1
    assert s["confidence_skip_counterfactual_correct"]==0
    assert s["ready_to_promote"] is False
    assert s["model_trained"] is False

    examples=verified_forward_feature_examples(tmp_path)
    assert len(examples)==3
    keyed={x.market_ticker:x for x in examples}
    assert keyed[a.market_ticker].label_yes==1
    assert keyed[b.market_ticker].label_yes==0  # NOT the captured dummy label
    assert keyed[c.market_ticker].label_yes==0
    assert all(x.checkpoint_ts_ns<=x.source_max_ts_ns["binance"]+NS for x in examples)


def test_tampered_official_outcome_or_forecast_never_becomes_training_label(tmp_path:Path):
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter,build_forward_snapshot,verified_forward_feature_examples,
        evaluate_forward_feature_evidence,
    )
    r,p=final_at(tmp_path,20)
    Forward480FeatureWriter(tmp_path).save(
        build_forward_snapshot(r,p,features(r))
    )
    settled=settle(tmp_path,r,"BELOW")
    assert len(verified_forward_feature_examples(tmp_path))==1
    payload=json.loads(settled.read_text())
    payload["actual_side"]="ABOVE" # inconsistent with official result=no
    settled.write_text(json.dumps(payload),encoding="utf-8")
    assert verified_forward_feature_examples(tmp_path)==[]
    summary=evaluate_forward_feature_evidence(tmp_path)
    assert summary["invalid_settlements"]==1
    assert summary["verified_feature_examples"]==0
