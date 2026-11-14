"""Frozen TimesFM3 + learned settlement head must remain research-only."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "research" / "timesfm3_market_adapter.py"


def load():
    spec = importlib.util.spec_from_file_location("timesfm3_market_adapter", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample(n=120):
    m = load()
    records = []
    prediction = []
    for i in range(n):
        # Deliberately chronological markets with two distinct label classes.
        strike = 100.0
        forecast_price = 99.2 if i % 4 == 0 else 100.8
        label = int(forecast_price > 100.0) if i % 7 else int(forecast_price < 100.0)
        rec = {
            "ticker": f"KXBTC15M-{i:04}",
            "market_date": f"2026-09-{1+i//96:02}",
            "checkpoint_ts_ns": 1_800_000_000_000_000_000 + i * 900_000_000_000,
            "label_yes": label,
            "strike": strike,
            "strike_type": "greater",
            "context": [99.0 + 0.001*j for j in range(256)],
        }
        records.append(rec)
        prediction.append({"ticker":rec["ticker"],"forecast_price":forecast_price})
    p = {
        "experiment_kind": m.KIND,
        "market_scope": "DEVELOPMENT_ONLY",
        "model_checkpoint": m.MODEL,
        "sealed_current_lockbox_opened":False,
        "dataset_sha256":"a"*64,
        "git_sha":"b"*40,
        "records":records,
    }
    p["input_sha256"] = m._digest(p)
    f = {"checkpoint":m.MODEL,"input_sha256":p["input_sha256"],"results":prediction}
    return p,f


def test_prepare_selection_only_development_and_rejects_ambiguous_chronology():
    m=load()
    groups=[(f"m{i:03}",1000+i*100,1050+i*100) for i in range(20)]
    eligible=set(f"m{i:03}" for i in range(16))
    chosen=m.select_development_markets(groups,eligible)
    assert chosen == tuple(f"m{i:03}" for i in range(16))
    assert "m019" not in chosen
    with pytest.raises(ValueError,match="duplicate"):
        m.select_development_markets(groups + [groups[0]],eligible)
    bad=list(groups);bad[5]=(bad[5][0],bad[4][1],bad[5][2])
    with pytest.raises(ValueError,match="chronolog"):
        m.select_development_markets(bad,eligible)


def test_temporal_fit_calibrate_evaluate_has_disjoint_two_market_embargo():
    m=load()
    parts=m.chronological_partitions(tuple(f"m{i:03}" for i in range(100)),minimum=30,embargo=2)
    assert len(parts["fit"]) >= 60
    for name in ("fit","calibration","evaluation"):
        assert parts[name] and len(set(parts[name])) == len(parts[name])
    assert parts["fit"][-1] < parts["calibration"][0] < parts["calibration"][-1] < parts["evaluation"][0]
    assert int(parts["calibration"][0][1:]) - int(parts["fit"][-1][1:]) >= 3
    assert int(parts["evaluation"][0][1:]) - int(parts["calibration"][-1][1:]) >= 3
    assert set(parts["fit"]).isdisjoint(parts["evaluation"])
    with pytest.raises(ValueError,match="at least"):
        m.chronological_partitions(tuple(f"m{i}" for i in range(10)),minimum=30)


def test_features_do_not_depend_on_future_label_and_reject_invalid_prices():
    m=load()
    p,f = _sample()
    rec=dict(p["records"][2])
    features=m.adapter_features(rec, f["results"][2]["forecast_price"])
    rec["label_yes"]=1-rec["label_yes"]
    assert m.adapter_features(rec,f["results"][2]["forecast_price"]) == features
    assert all(math.isfinite(x) for x in features)
    with pytest.raises(ValueError):
        m.adapter_features(rec,-4.0)
    with pytest.raises(ValueError):
        m.adapter_features({**rec, "context":[0.0]},100.0)


def test_train_head_reports_calibrated_probabilities_and_no_deployment():
    m=load()
    prepared,forecast=_sample()
    result=m.train_adapter(prepared,forecast,minimum=30)
    assert result["experiment_kind"]=="TIMESFM3_SETTLEMENT_ADAPTER_RESEARCH"
    assert result["promotion_eligible"] is False
    assert result["live_model_changed"] is False
    assert result["timesfm3_weights_finetuned"] is False
    assert result["market_count"] == 120
    assert result["evaluation_markets"] >= 10
    assert 0 <= result["adapter_brier"] <= 1
    assert 0 <= result["adapter_accuracy"] <= 1
    assert 0 <= result["adapter_69_gate_coverage"] <= 1
    assert result["trade_pnl_dollars"] is None
    assert result["adapter_parameters"]["fit_markets"] > 0
    assert "test_markets" not in result["adapter_parameters"]


def test_train_head_rejects_prediction_mismatch_duplicate_and_modified_preparation():
    m=load()
    prepared,forecast=_sample()
    with pytest.raises(ValueError,match="hash"):
        m.train_adapter({**prepared,"dataset_sha256":"f"*64},forecast,minimum=30)
    with pytest.raises(ValueError,match="hash"):
        m.train_adapter(prepared,{**forecast,"input_sha256":"c"*64},minimum=30)
    with pytest.raises(ValueError,match="missing|extra|duplicate"):
        m.train_adapter(prepared,{**forecast,"results":forecast["results"][:-1]},minimum=30)
    with pytest.raises(ValueError,match="duplicate"):
        m.train_adapter(prepared,{**forecast,"results":forecast["results"]+[forecast["results"][0]]},minimum=30)


def test_noncommercial_gate_precedes_checkpoint_import(tmp_path):
    m=load()
    with pytest.raises(ValueError,match="non-commercial|license"):
        m.run_infer(tmp_path/"does-not-exist.json",tmp_path/"result.json",
                    accepted_noncommercial_license=False)
