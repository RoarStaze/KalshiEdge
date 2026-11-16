"""Test-first, read-only signal-error and matched-coverage analysis.

The synthetic improvement fixture is intentionally NOT historical proof.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from kalshi_edge.bootstrap.signal_fusion_480 import _Example, build_signal_vector
from kalshi_edge.bootstrap.model_signal_error_audit_480 import (
    AUDIT_POLICY,
    THRESHOLDS_PERCENT,
    classify_signal_conflicts,
    evaluate_error_guard_audit,
    main,
)

HASH = "a" * 64
T0 = 1_900_000_000_000_000_000


def _example(index: int, probability: float, actual: int, sign: int,
             *, risk: float = 1.) -> _Example:
    # Synthetic outcomes are NEVER inputs to the candidate selection.
    values = {
        "brti_distance_to_strike_bps": sign * 12.,
        "brti_binance_gap_bps": 0.,
        "btc_return_15s": sign * .0008,
        "btc_return_15s_available": 1.,
        "btc_return_60s": sign * .0016,
        "btc_return_60s_available": 1.,
        "btc_taker_buy_imbalance_60s": sign * .6,
        "btc_base_volume_60s": 30.,
        "kalshi_yes_taker_imbalance_60s": sign * .5,
    }
    v=build_signal_vector(probability,values,risk_z=risk)
    assert v is not None
    return _Example(
        ticker=f"KXBTC15M-TEST-{index:04}",
        checkpoint_ts_ns=T0 + index * 900_000_000_000,
        fetched_ts_ns=T0 + index * 900_000_000_000 + 600_000_000_000,
        probability=probability, risk_z=risk, vector=v,
        actual_yes=actual,
    )


def _fixture():
    # Model favors YES for all. High-confidence model error at index 0
    # conflicts with four independent signal families. Item 3 is a rescued
    # correct forecast below .75. All are synthetic; NO empirical claims.
    return [
        _example(0,.90,0,-1),
        _example(1,.85,1,+1),
        _example(2,.81,1,+1),
        _example(3,.70,1,+1),
        _example(4,.65,0,-1),
    ]


def _set_sources(monkeypatch, rows):
    import kalshi_edge.bootstrap.model_signal_error_audit_480 as module
    monkeypatch.setattr(module,"_verified_examples",lambda root,sha:(
        rows, {"same_model_finals":len(rows),
               "eligible":len(rows),"missing_or_invalid":0}
    ))
    monkeypatch.setattr(module,"_forecasts",lambda root:[
        SimpleNamespace(market_ticker=r.ticker,reason=None) for r in rows
    ])
    return module


def test_conflict_families_are_causal_and_ignore_labels_and_model_magnitude():
    good = _example(1,.85,1,+1)
    bad = _example(0,.90,0,-1)
    assert set(classify_signal_conflicts(good)) == {
        "brti_strike_side", "candle_consensus",
        "btc_aggressor_flow", "kalshi_taker_flow",
    }
    assert all(not flag for flag in classify_signal_conflicts(good).values())
    assert all(classify_signal_conflicts(bad).values())
    changed_label=replace(bad,actual_yes=1)
    assert classify_signal_conflicts(changed_label)==classify_signal_conflicts(bad)
    # Same underlying signals but another YES probability magnitude.
    moved=_example(0,.65,0,-1)
    assert classify_signal_conflicts(moved)==classify_signal_conflicts(bad)


def test_uses_same_verified_market_cohort_and_thresholds_no_retraining(monkeypatch,tmp_path):
    _set_sources(monkeypatch,_fixture())
    out=evaluate_error_guard_audit(tmp_path,model_hash=HASH)
    assert out["status"]=="DESCRIPTIVE_ONLY"
    assert out["matched_settled_markets"]==5
    assert out["model_retraining"] is False
    assert out["live_gate_changed"] is False
    assert out["trades_executed"] is False
    assert out["policy"]==AUDIT_POLICY
    assert [r["threshold_percent"] for r in out["thresholds"]]==list(THRESHOLDS_PERCENT)
    for row in out["thresholds"]:
        k=row["original_gate"]["qualified"]
        for name in ("original_gate","conflict_priority","existing_blend_priority"):
            item=row[name]
            assert item["qualified"]==k
            assert item["skipped"]==5-k
            assert item["correct"]+item["incorrect"]==k
            assert item["coverage"]==pytest.approx(k/5)
        assert row["conflict_priority"]["replacements"]>=0
        assert row["conflict_priority"]["selected_below_original_gate"]>=0
    assert out["family_error_counts"]["brti_strike_side"]["flagged"]==2


def test_error_guard_can_reorder_without_refitting_or_artificial_probabilities(monkeypatch,tmp_path):
    _set_sources(monkeypatch,_fixture())
    out=evaluate_error_guard_audit(tmp_path,model_hash=HASH)
    row=next(item for item in out["thresholds"] if item["threshold_percent"]==75)
    assert row["original_gate"]["qualified"]==3
    assert row["original_gate"]["correct"]==2
    assert row["conflict_priority"]["qualified"]==3
    assert row["conflict_priority"]["correct"]==3
    assert row["conflict_priority"]["delta_correct_vs_gate"]==1
    assert row["conflict_priority"]["replacements"]==1
    assert row["conflict_priority"]["selected_below_original_gate"]==1
    assert row["original_gate"]["selected_below_original_gate"]==0
    assert row["original_gate"]["replacements"]==0
    assert out["underlying_model_probability_modified"] is False
    assert out["experimental_probabilities_used_for_live"] is False


def test_guard_not_guaranteed_improvement_with_same_precommitted_order(monkeypatch,tmp_path):
    rows=[replace(row,actual_yes=1-row.actual_yes) for row in _fixture()]
    _set_sources(monkeypatch,rows)
    out=evaluate_error_guard_audit(tmp_path,model_hash=HASH)
    row=next(item for item in out["thresholds"] if item["threshold_percent"]==75)
    assert row["conflict_priority"]["delta_correct_vs_gate"]==-1
    assert row["conflict_priority"]["replacements"]==1


def test_both_strategies_preserve_coincidence_flip_veto(monkeypatch,tmp_path):
    rows=_fixture()
    rows[1]=replace(rows[1],risk_z=.1)
    _set_sources(monkeypatch,rows)
    out=evaluate_error_guard_audit(tmp_path,model_hash=HASH)
    row=next(item for item in out["thresholds"] if item["threshold_percent"]==75)
    assert row["original_gate"]["qualified"]==2
    assert row["conflict_priority"]["qualified"]==2
    assert row["existing_blend_priority"]["qualified"]==2
    assert out["excluded"]["recorded_brti_flip_veto"]==1


def test_missing_settlement_or_snapshot_does_not_create_false_result(monkeypatch,tmp_path):
    _set_sources(monkeypatch,[])
    result=evaluate_error_guard_audit(tmp_path,model_hash=HASH)
    assert result["status"]=="NO_VERIFIED_PAIRED_EVIDENCE"
    assert result["matched_settled_markets"]==0
    assert all(row["original_gate"]["accuracy"] is None for row in result["thresholds"])
    assert all(row["conflict_priority"]["accuracy"] is None for row in result["thresholds"])
    assert result["family_error_counts"]["brti_strike_side"]["flagged"]==0


def test_cli_text_json_and_immutable_evidence(monkeypatch,tmp_path,capsys):
    _set_sources(monkeypatch,_fixture())
    args=["--root",str(tmp_path),"--model-hash",HASH]
    before=list(tmp_path.rglob("*"))
    assert main(args)==0
    text=capsys.readouterr().out
    assert "MATCHED COVERAGE" in text
    assert "63%" in text and "82%" in text
    assert "DESCRIPTIVE ONLY" in text
    assert main(args+["--json"])==0
    data=json.loads(capsys.readouterr().out)
    assert data["matched_settled_markets"]==5
    assert data["thresholds"][0]["threshold_percent"]==63
    assert data["thresholds"][-1]["threshold_percent"]==82
    assert list(tmp_path.rglob("*"))==before
