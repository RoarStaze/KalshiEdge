"""Forensic analysis is read-only; actual Kalshi settlements are sole labels.

This synthetic fixture checks accounting, NOT a forecast performance gain.
"""
from __future__ import annotations

import json

from kalshi_edge.bootstrap.final_forecast import (
    FinalForecastRecord, FinalForecastWriter,
)
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
    Forward480FeatureWriter, build_forward_snapshot,
)
from kalshi_edge.bootstrap.types import FeatureRow

from kalshi_edge.bootstrap.skip_reversal_forensics_480 import (
    analyze_skip_reversal_forensics, main,
)

NS = 1_000_000_000
BASE = 1_900_000_000 * NS
MODEL = "a" * 64


def record(root, index, *, p=.9, result=True, decision=None, reason=None,
           risk=None, settlement=True, hash=MODEL, checkpoint=480,
           snapshot=False, btc=None, brti_bps=1.0):
    scheduled = BASE + index * 900 * NS
    close = scheduled + checkpoint * NS
    side = "ABOVE" if p >= .5 else "BELOW"
    decision = decision or side
    ticker = f"KXBTC15M-FORENSIC-{index:04d}"
    final = FinalForecastRecord(
        market_ticker=ticker, strike=100_000.,
        scheduled_ts_ns=scheduled, prediction_ts_ns=scheduled,
        checkpoint_seconds=checkpoint, seconds_remaining=float(checkpoint),
        forecast=decision, reason=reason,
        p_yes=p, p_no=1-p,
        confidence=2 * abs(p-.5), directional_probability=max(p, 1-p),
        coin_flip_risk_z=risk,
        coin_flip_assessment=("HIGH" if risk is not None and risk <= .2 else "LOW"),
        model_hash=hash,
    )
    path = FinalForecastWriter(root).save(final, market_close_ts_ns=close)
    assert path is not None
    if snapshot:
        row = FeatureRow(
            market_ticker=ticker, checkpoint_ts_ns=scheduled, label_yes=0,
            features={
                "seconds_remaining":float(checkpoint),
                "brti_distance_to_strike_bps":brti_bps,
                "brti_binance_gap_bps":brti_bps / 2.,
                "btc_close":float(btc) if btc is not None else 100_001.,
            },
            source_max_ts_ns={
                "binance":scheduled-NS,
                "brti":scheduled-NS,
            },
        )
        snap=build_forward_snapshot(final,path,row)
        Forward480FeatureWriter(root).save(snap)
    if settlement:
        _save_settlement(root,final,OfficialSettlement(
            market_ticker=ticker,market_close_ts_ns=close,
            result="yes" if result else "no",
            actual_side="ABOVE" if result else "BELOW",
            source="kalshi-public-market-api",fetched_ts_ns=close+60*NS,
            market_status="settled",
        ))
    return final


def fixture(root):
    record(root,1,p=.90,result=True,risk=.9,snapshot=True,brti_bps=2.,btc=100001.)
    record(root,2,p=.85,result=False,risk=.45,snapshot=True,brti_bps=-2.,btc=100003.)
    record(root,3,p=.62,result=True,decision="SKIP",
           reason="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT",
           risk=.8,snapshot=True,brti_bps=3.,btc=100001.)
    record(root,4,p=.60,result=False,decision="SKIP",
           reason="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT",risk=.9)
    record(root,5,p=.91,result=True,decision="SKIP",
           reason="COIN_FLIP_RISK_HIGH",risk=.10)
    record(root,6,p=.94,result=False,decision="SKIP",
           reason="COIN_FLIP_RISK_HIGH",risk=.12)
    record(root,7,p=.9,result=True,decision="SKIP",reason="STALE_BRTI",risk=.3)
    record(root,8,p=.8,result=True,hash="b"*64,risk=.4)
    record(root,9,p=.8,result=True,checkpoint=240,risk=.5)
    record(root,10,p=.8,result=True,settlement=False,risk=.4)


def test_exact_reason_attribution_and_false_skips_from_official_settlements(tmp_path):
    fixture(tmp_path)
    r=analyze_skip_reversal_forensics(tmp_path,model_hash=MODEL)
    assert r["status"]=="DESCRIPTIVE_ONLY"
    assert r["matched_settled_with_usable_probability"]==6
    assert r["qualified"]["total"]==2
    assert r["qualified"]["correct"]==1
    assert r["qualified"]["incorrect"]==1
    assert r["probability_skips"]["total"]==2
    assert r["probability_skips"]["would_have_been_correct"]==1
    assert r["probability_skips"]["would_have_been_wrong"]==1
    assert r["brti_flip_veto_skips"]["total"]==2
    assert r["brti_flip_veto_skips"]["would_have_been_correct"]==1
    assert r["brti_flip_veto_skips"]["would_have_been_wrong"]==1
    assert r["excluded"]["other_unhealthy_skip"]==1
    assert r["excluded"]["different_model"]==1
    assert r["excluded"]["non_480_checkpoint"]==1
    assert r["excluded"]["unsettled"]==1
    assert r["actual_issued_accuracy"]==.5
    assert r["actual_issued_coverage"]==2/6
    assert r["research_only"]["forecast_probability_unchanged"] is True
    assert r["research_only"]["trades_executed"] is False


def test_brti_flip_veto_and_reference_disagreement_have_separate_denominators(tmp_path):
    fixture(tmp_path)
    r=analyze_skip_reversal_forensics(tmp_path,model_hash=MODEL)
    high=r["risk_strata"]["at_or_below_0_20"]
    assert high["total"]==2 and high["would_be_correct"]==1
    assert r["reference_alignment"]["verified_checkpoints"]==3
    assert r["reference_alignment"]["brti_vs_binance_side_disagreements"]==1
    assert r["reference_alignment"]["disagreement_model_wrong"]==1
    assert r["reference_alignment"]["snapshots_unavailable_or_invalid"]==3
    assert r["reference_alignment"]["used_post_checkpoint_prices"] is False


def test_other_skip_cannot_be_retrospectively_rescued_as_healthy(tmp_path):
    record(tmp_path,1,p=.95,result=True,decision="SKIP",
           reason="MISSING_CHECKPOINT_PREDICTION",risk=None)
    r=analyze_skip_reversal_forensics(tmp_path,model_hash=MODEL)
    assert r["matched_settled_with_usable_probability"]==0
    assert r["excluded"]["other_unhealthy_skip"]==1
    assert r["probability_skips"]["total"]==0
    assert r["brti_flip_veto_skips"]["total"]==0


def test_empty_dataset_does_not_fabricate_accuracy(tmp_path):
    r=analyze_skip_reversal_forensics(tmp_path,model_hash=MODEL)
    assert r["status"]=="NO_MATCHED_SETTLEMENTS"
    assert r["actual_issued_accuracy"] is None
    assert r["actual_issued_coverage"] is None
    assert r["probability_skips"]["would_have_been_correct"]==0


def test_command_table_json_no_mutation(tmp_path,capsys):
    fixture(tmp_path)
    before={str(p):p.read_bytes() for p in tmp_path.rglob("*.json")}
    cmd=["--root",str(tmp_path),"--model-hash",MODEL]
    assert main(cmd)==0
    text=capsys.readouterr().out
    assert "BRTI FLIP VETO" in text
    assert "PROBABILITY SKIPS" in text
    assert "6" in text
    assert "DESCRIPTIVE ONLY" in text
    assert main(cmd+["--json"])==0
    data=json.loads(capsys.readouterr().out)
    assert data["matched_settled_with_usable_probability"]==6
    assert {str(p):p.read_bytes() for p in tmp_path.rglob("*.json")}==before
