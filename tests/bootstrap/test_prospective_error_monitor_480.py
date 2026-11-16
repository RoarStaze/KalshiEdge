"""Frozen forward-only monitoring contracts; all fixtures are synthetic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
    Forward480FeatureWriter, build_forward_snapshot,
)
from kalshi_edge.bootstrap.types import FeatureRow
from kalshi_edge.bootstrap.prospective_error_monitor_480 import (
    freeze_prospective_monitor,
    evaluate_prospective_monitor,
    load_frozen_manifest,
    main,
)

NS=1_000_000_000
START=1_900_000_000_000_000_000
MODEL="a"*64


def _record(root: Path, index: int, *, p:float|None=.85,
            actual:bool=True, kind:str="issued", risk:float|None=2.,
            brti:float=4., btc:float=100_003.,
            scheduled:int|None=None, future:bool=True,
            snapshot:bool=True, settlement:bool=True,
            hash:str=MODEL, checkpoint:int=480, fusion:bool=False):
    ts=scheduled if scheduled is not None else START + index*900*NS
    close=ts+checkpoint*NS
    ticker=f"KXBTC15M-PROSPECTIVE-{index:04d}"
    if kind=="issued":
        decision="ABOVE" if p is not None and p>=.5 else "BELOW"
        reason=None
    elif kind=="prob_skip":
        decision="SKIP"
        reason="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_69_PERCENT"
    elif kind=="veto":
        decision="SKIP"
        reason="COIN_FLIP_RISK_HIGH"
    elif kind=="unhealthy":
        decision="SKIP"
        reason="STALE_BRTI"
    else:
        raise ValueError(kind)
    f=FinalForecastRecord(
        market_ticker=ticker, strike=100_000., scheduled_ts_ns=ts,
        prediction_ts_ns=ts,checkpoint_seconds=checkpoint,
        seconds_remaining=float(checkpoint),forecast=decision,reason=reason,
        p_yes=p,p_no=None if p is None else 1-p,
        directional_probability=None if p is None else max(p,1-p),
        confidence=None if p is None else 2*abs(p-.5),
        coin_flip_risk_z=risk,
        coin_flip_assessment=("UNAVAILABLE" if risk is None else
                              "HIGH" if risk<=.2 else "LOW"),
        model_hash=hash,
        fusion_hash="f"*64 if fusion else None,
        unfused_p_yes=p if fusion else None,
    )
    path=FinalForecastWriter(root).save(f,market_close_ts_ns=close)
    assert path is not None
    if snapshot and checkpoint==480:
        row=None
        if kind!="unhealthy":
            row=FeatureRow(
                market_ticker=ticker, checkpoint_ts_ns=ts,label_yes=0,
                features={
                    "brti_distance_to_strike_bps":brti,
                    "brti_binance_gap_bps":brti-(btc-100_000.)/10.,
                    "btc_close":btc,
                },
                source_max_ts_ns={
                    "brti":ts-NS, "binance":ts-NS,
                },
            )
        snap=build_forward_snapshot(
            f,path,row,
            reason="UNHEALTHY_CHECKPOINT_INPUTS" if row is None else None,
        )
        Forward480FeatureWriter(root).save(snap)
    if settlement:
        _save_settlement(root,f,OfficialSettlement(
            market_ticker=ticker, market_close_ts_ns=close,
            result="yes" if actual else "no",
            actual_side="ABOVE" if actual else "BELOW",
            source="kalshi-public-market-api",
            market_status="settled",
            fetched_ts_ns=close+60*NS,
        ))
    return f


def test_freeze_first_and_refuse_in_sample_tuning_and_tamper(tmp_path):
    report=evaluate_prospective_monitor(tmp_path,now_ns=START+30_000*NS)
    assert report["status"]=="NOT_FROZEN"
    frozen=freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START)
    assert frozen["model_hash"]==MODEL
    assert frozen["started_at_ns"]==START
    assert frozen["minimum_directional_probability"]==pytest.approx(.69)
    assert frozen["brti_flip_veto_z"]==pytest.approx(.2)
    assert frozen["fixed_horizon_recorded_markets"]==480
    assert frozen["primary_comparison"]=="REFERENCE_DISAGREEMENT_VS_AGREEMENT"
    assert load_frozen_manifest(tmp_path)==frozen
    with pytest.raises(FileExistsError):
        freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START+NS)
    manifest=tmp_path/"research"/"prospective_monitor_480"/"frozen_69_baseline.json"
    body=json.loads(manifest.read_text())
    body["minimum_directional_probability"]=.50
    manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError,match="integrity"):
        load_frozen_manifest(tmp_path)


def test_only_fresh_post_freeze_properly_settled_current_model_markets(tmp_path):
    _record(tmp_path,0,p=.95,actual=True) # created BEFORE freeze
    freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START)
    _record(tmp_path,1,p=.90,actual=True,risk=2.,brti=5.,btc=100002.)
    _record(tmp_path,2,p=.90,actual=False,risk=.5,brti=-5.,btc=100002.)
    _record(tmp_path,3,p=.60,kind="prob_skip",actual=True,risk=2.,
            brti=5.,btc=100002.)
    _record(tmp_path,4,p=.55,kind="prob_skip",actual=False,risk=.5,
            brti=-5.,btc=99998.)
    _record(tmp_path,5,p=.80,kind="veto",actual=False,risk=.1,
            brti=5.,btc=100003.)
    _record(tmp_path,6,p=None,kind="unhealthy",actual=True,risk=None)
    _record(tmp_path,7,p=.90,settlement=False)
    _record(tmp_path,8,p=.95,hash="b"*64)
    _record(tmp_path,9,p=.95,checkpoint=240)
    _record(tmp_path,10,p=.95,fusion=True)
    report=evaluate_prospective_monitor(tmp_path,now_ns=START+40_000*NS)
    assert report["status"]=="COLLECTING_NEW_SETTLEMENTS"
    assert report["frozen_baseline"]["model_hash"]==MODEL
    assert report["recorded_post_freeze"]==7
    assert report["settled_post_freeze"]==6
    assert report["pending_settlements"]==1
    assert report["issued"]["total"]==2
    assert report["issued"]["correct"]==1
    assert report["issued"]["wrong"]==1
    assert report["operational_coverage"]==pytest.approx(2/6)
    assert report["probability_skips"]["total"]==2
    assert report["probability_skips"]["counterfactual_correct"]==1
    assert report["probability_skips"]["counterfactual_wrong"]==1
    assert report["flip_veto_skips"]["total"]==1
    assert report["unhealthy_skips"]==1
    assert report["model_scored_settled"]==5
    assert report["candidate_joint_under_live_gate"]["total"]==1
    assert report["candidate_joint_under_live_gate"]["correct"]==1
    assert report["reference_by_model_outcome"]["disagree"]["total"]==1
    assert report["reference_by_model_outcome"]["disagree"]["wrong"]==1
    assert report["reference_by_model_outcome"]["agree_with_model"]["total"]==3
    assert report["recorded_exclusions"]["before_freeze"]==1
    assert report["recorded_exclusions"]["different_model"]==1
    assert report["recorded_exclusions"]["non_480_checkpoint"]==1
    assert report["recorded_exclusions"]["fusion_enabled"]==1
    assert report["confidence_quality"]["brier"] is not None
    assert report["model_changed"] is False
    assert report["live_policy_changed"] is False
    assert report["trades_executed"] is False


def test_snapshot_missing_fails_closed_for_signal_analysis(tmp_path):
    freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START)
    _record(tmp_path,1,p=.61,actual=True,kind="prob_skip",
            risk=2.,snapshot=False)
    r=evaluate_prospective_monitor(tmp_path,now_ns=START+12_000*NS)
    assert r["settled_post_freeze"]==1
    assert r["probability_skips"]["total"]==1
    assert r["reference_by_model_outcome"]["unavailable"]["total"]==1
    assert r["candidate_joint_under_live_gate"]["total"]==0
    assert r["missing_or_invalid_causal_snapshots"]==1


def test_policy_drift_is_reported_not_accepted_as_independent_confirmation(tmp_path):
    freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START)
    _record(tmp_path,1,p=.8,actual=True,kind="prob_skip",risk=2.)
    r=evaluate_prospective_monitor(tmp_path,now_ns=START+10_000*NS)
    assert r["status"]=="POLICY_INCONSISTENCY"
    assert r["policy_inconsistencies"]==1
    assert r["candidate_joint_under_live_gate"]["total"]==0


def test_earliest_recorded_horizon_is_fixed_before_outcomes(tmp_path):
    freeze_prospective_monitor(
        tmp_path,model_hash=MODEL,now_ns=START,target_recorded_markets=2
    )
    _record(tmp_path,1,p=.85,actual=True)
    _record(tmp_path,2,p=.88,actual=False,settlement=False)
    _record(tmp_path,3,p=.90,actual=True)
    r=evaluate_prospective_monitor(tmp_path,now_ns=START+20_000*NS)
    assert r["recorded_post_freeze"]==2
    assert r["pending_settlements"]==1
    assert r["status"]=="COLLECTING_NEW_SETTLEMENTS"
    assert r["issued"]["total"]==1  # third forecast cannot replace pending second


def test_prospective_status_cli_is_read_only_and_freeze_is_one_time(tmp_path,capsys,monkeypatch):
    import kalshi_edge.bootstrap.prospective_error_monitor_480 as monitor
    monkeypatch.setattr(monitor.time,"time_ns",lambda:START)
    args=["--root",str(tmp_path),"--model-hash",MODEL]
    assert main(args+["--freeze","--json"])==0
    x=json.loads(capsys.readouterr().out)
    assert x["started_at_ns"]==START
    manifest=tmp_path/"research"/"prospective_monitor_480"/"frozen_69_baseline.json"
    before=manifest.read_bytes()
    monkeypatch.setattr(monitor.time,"time_ns",lambda:START+NS)
    assert main(args+["--json"])==0
    y=json.loads(capsys.readouterr().out)
    assert y["status"]=="COLLECTING_NEW_SETTLEMENTS"
    assert y["recorded_post_freeze"]==0
    assert manifest.read_bytes()==before
    assert main(args+["--freeze"])==2
    assert manifest.read_bytes()==before


def test_model_hash_change_never_mix_versions(tmp_path):
    freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START)
    _record(tmp_path,1,p=.86,hash="b"*64)
    r=evaluate_prospective_monitor(tmp_path,now_ns=START+10_000*NS)
    assert r["recorded_post_freeze"]==0
    assert r["recorded_exclusions"]["different_model"]==1


def test_old_frozen_63_manifest_is_never_edited_by_new_69_registration(tmp_path):
    old=tmp_path/"research"/"prospective_monitor_480"/"frozen_baseline.json"
    old.parent.mkdir(parents=True)
    old.write_bytes(b'{"archived_policy":"63%-probability","sha256":"not-modified"}\n')
    saved=old.read_bytes()
    new=freeze_prospective_monitor(tmp_path,model_hash=MODEL,now_ns=START)
    assert new["minimum_directional_probability"]==.69
    assert old.read_bytes()==saved
    assert (old.parent/"frozen_69_baseline.json").is_file()
