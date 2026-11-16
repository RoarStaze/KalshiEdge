"""Synthetic accounting tests for read-only paired BRTI rescue research."""
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
from kalshi_edge.bootstrap.brti_joint_rescue_audit_480 import (
    evaluate_brti_joint_rescue, main,
)

NS = 1_000_000_000
BASE = 1_900_000_000 * NS
MODEL = "a" * 64


def _record(root: Path, i: int, *, p: float=.8, actual: bool=True,
            kind: str="legacy", z: float=2., brti: float=5.,
            btc: float=100_002., snapshot: bool=True,
            model_hash: str=MODEL, seconds: int=480,
            settled: bool=True, unhealthy: bool=False,
            stale: bool=False, fused: bool=False):
    ts=BASE+i*900*NS
    close=ts+seconds*NS
    ticker=f"KXBTC15M-JOINT-{i:04d}"
    if kind=="legacy":
        direction="SKIP"
        why="CONFIDENCE_BELOW_63_PERCENT"
    elif kind=="current":
        direction="SKIP"
        why="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT"
    elif kind=="veto":
        direction="SKIP"
        why="COIN_FLIP_RISK_HIGH"
    else:
        direction="ABOVE" if p>=.5 else "BELOW"
        why=None
    if unhealthy:
        direction="SKIP"
        why="STALE_BRTI"
    f=FinalForecastRecord(
        market_ticker=ticker,strike=100000.,scheduled_ts_ns=ts,
        prediction_ts_ns=ts, checkpoint_seconds=seconds,
        seconds_remaining=float(seconds),
        forecast=direction,reason=why,model_hash=model_hash,
        p_yes=p,p_no=1-p,confidence=2*abs(p-.5),
        directional_probability=max(p,1-p),coin_flip_risk_z=z,
        coin_flip_assessment="HIGH" if z<=.20 else "LOW",
        fusion_hash=("f"*64 if fused else None),
        unfused_p_yes=(p if fused else None),
    )
    file=FinalForecastWriter(root).save(f,market_close_ts_ns=close)
    assert file is not None
    # 240s legacy records cannot lawfully have a 480s immutable snapshot.
    if snapshot and seconds == 480:
        row=FeatureRow(
            market_ticker=ticker,checkpoint_ts_ns=ts,label_yes=0,
            features={
                "brti_distance_to_strike_bps":brti,
                "brti_binance_gap_bps":brti-(btc-100_000.)/10.,
                "btc_close":btc,
            },
            source_max_ts_ns={
                "brti":ts-(20*NS if stale else NS),
                "binance":ts-NS,
            },
        )
        snap=build_forward_snapshot(f,file,row)
        Forward480FeatureWriter(root).save(snap)
    if settled:
        _save_settlement(root,f,OfficialSettlement(
            market_ticker=ticker,market_close_ts_ns=close,
            result="yes" if actual else "no",
            actual_side="ABOVE" if actual else "BELOW",
            fetched_ts_ns=close+60*NS,market_status="settled",
            source="kalshi-public-market-api",
        ))
    return f


def _fixture(root):
    _record(root,1,p=.79,actual=True,brti=+5,btc=100_002.)
    _record(root,2,p=.75,actual=False,brti=+5,btc=100_003.)
    _record(root,3,p=.60,actual=True,brti=+5,btc=100_002.)
    _record(root,4,p=.72,actual=True,z=.1,brti=+5,btc=100_002.)
    _record(root,5,p=.72,actual=False,brti=+5,btc=99_998.)
    _record(root,6,p=.85,kind="issued",actual=True,brti=+5,btc=100_002.)
    _record(root,7,p=.90,kind="issued",actual=False,brti=-5,btc=99_997.)
    _record(root,8,p=.90,kind="veto",z=.1,actual=True,brti=+5,btc=100_002.)
    _record(root,9,p=.90,actual=True,unhealthy=True)
    _record(root,10,p=.90,actual=True,model_hash="b"*64)
    _record(root,11,p=.90,actual=True,seconds=240)
    _record(root,12,p=.90,actual=True,settled=False)
    _record(root,13,p=.78,actual=True,z=3.,snapshot=False)
    _record(root,14,p=.80,actual=False,z=2.,brti=-5,btc=99_998.)


def test_joint_rescue_conjunction_and_live_replay_are_independent(tmp_path):
    _fixture(tmp_path)
    r=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    assert r["status"]=="DESCRIPTIVE_ONLY"
    assert r["matched_settled_markets"]==10
    assert r["historical_strength_skips"]=={
        "total":7,"correct":4,"wrong":3,"accuracy":4/7,
    }
    assert r["joint_risk_gt_1_and_reference_agreement"]=={
        "total":4,"correct":2,"wrong":2,"accuracy":.5,
    }
    assert r["joint_plus_model_side_agreement"]=={
        "total":3,"correct":2,"wrong":1,"accuracy":2/3,
    }
    assert r["joint_above_current_gate"]["total"]==3
    assert r["joint_above_current_gate"]["correct"]==1
    assert r["joint_at_or_below_current_gate"]["total"]==1
    assert r["joint_at_or_below_current_gate"]["correct"]==1
    assert r["current_63_gate_counterfactual"]["qualified"]==7
    assert r["current_63_gate_counterfactual"]["correct"]==3
    assert r["current_63_gate_counterfactual"]["incorrect"]==4
    assert r["current_63_gate_counterfactual"]["skipped"]==3
    assert r["current_63_gate_counterfactual"]["qualified_legacy_skips"]==5
    assert r["current_63_gate_counterfactual"]["coverage"]==.7
    assert r["current_63_gate_counterfactual"]["accuracy"]==3/7


def test_reference_cross_alignment_missing_stale_ties_and_model_disagreement(tmp_path):
    _fixture(tmp_path)
    r=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    assert r["reference_status"]=={"agree":8,"disagree":1,"unavailable":1}
    assert r["legacy_skip_reference_status"]=={"agree":5,"disagree":1,"unavailable":1}
    assert r["joint_reference_against_model"]["total"]==1
    assert r["joint_reference_against_model"]["wrong"]==1
    assert r["excluded"]["other_unhealthy_skip"]==1
    assert r["excluded"]["different_model"]==1
    assert r["excluded"]["non_480_checkpoint"]==1
    assert r["excluded"]["unsettled"]==1

    # Causal safety: a reference snapshot observed 20 seconds before the
    # checkpoint is not considered fresh even when hash-valid.
    _record(tmp_path,15,p=.82,actual=True,z=2.,stale=True)
    _record(tmp_path,16,p=.82,actual=True,z=2.,brti=0.,btc=100_001.)
    out=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    assert out["reference_status"]["unavailable"]==3
    assert out["joint_risk_gt_1_and_reference_agreement"]["total"]==4


def test_individual_probability_bins_reject_spurious_coverage_claims(tmp_path):
    _fixture(tmp_path)
    r=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    bands={b["band"]:b for b in r["historical_skip_probability_bands"]}
    assert bands["[0.50,0.63]"]["historical_skips"]["total"]==1
    assert bands["[0.50,0.63]"]["joint_high_risk_agree"]["correct"]==1
    assert bands["(0.63,0.70]"]["historical_skips"]["total"]==0
    assert bands["(0.70,0.80]"]["historical_skips"]["total"]==6
    assert bands["(0.70,0.80]"]["joint_high_risk_agree"]["total"]==3
    assert r["historical_strength_skip_reasons"]=={
        "CONFIDENCE_BELOW_63_PERCENT":7,
    }
    assert r["research_only"]["model_retraining"] is False
    assert r["research_only"]["live_gate_changed"] is False
    assert r["research_only"]["trades_executed"] is False
    assert r["research_only"]["retrospective_rescue_only"] is True


def test_disallow_corrupted_forecast_snapshot_and_unhealthy_mixes(tmp_path):
    f=_record(tmp_path,1,p=.87,z=2.,actual=True)
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        FEATURE_DIR, _day, _close_ns
    )
    path=tmp_path/FEATURE_DIR/_day(_close_ns(f))/f"{f.market_ticker}.json"
    data=json.loads(path.read_text())
    data["features"]["brti_distance_to_strike_bps"]=-50.0
    path.write_text(json.dumps(data))
    _record(tmp_path,2,p=.9,z=2.,actual=True,fused=True)
    r=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    assert r["matched_settled_markets"]==1
    assert r["reference_status"]["unavailable"]==1
    assert r["excluded"]["fused_probability_record"]==1
    assert r["joint_risk_gt_1_and_reference_agreement"]["total"]==0


def test_empty_dataset_and_strict_gate_boundary(tmp_path):
    r=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    assert r["status"]=="NO_VERIFIED_SETTLED_MARKETS"
    assert r["current_63_gate_counterfactual"]["accuracy"] is None
    _record(tmp_path,1,p=.63,actual=True,z=1.5)
    r=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    assert r["current_63_gate_counterfactual"]["qualified"]==0
    assert r["joint_at_or_below_current_gate"]["total"]==1


def test_no_data_writes_and_table_json_cli(tmp_path,capsys):
    _fixture(tmp_path)
    before={p:p.read_bytes() for p in tmp_path.rglob("*.json")}
    args=["--root",str(tmp_path),"--model-hash",MODEL]
    assert main(args)==0
    shown=capsys.readouterr().out
    assert "JOINT BRTI RESCUE" in shown
    assert "HISTORICAL STRENGTH SKIPs" in shown
    assert "LIVE 63% REPLAY" in shown
    assert main(args+["--json"])==0
    report=json.loads(capsys.readouterr().out)
    assert report["matched_settled_markets"]==10
    assert {p:p.read_bytes() for p in tmp_path.rglob("*.json")}==before



def test_exactly_half_probability_is_accounted_for_in_first_fixed_band(tmp_path):
    _record(tmp_path,1,p=.5,actual=True,z=2.)
    out=evaluate_brti_joint_rescue(tmp_path,model_hash=MODEL)
    bands={r["band"]:r for r in out["historical_skip_probability_bands"]}
    assert bands["[0.50,0.63]"]["historical_skips"]["total"]==1
    assert sum(row["historical_skips"]["total"]
               for row in out["historical_skip_probability_bands"])==1
    assert out["current_63_gate_counterfactual"]["qualified"]==0
