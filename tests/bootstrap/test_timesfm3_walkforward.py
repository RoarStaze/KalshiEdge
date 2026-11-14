"""Chronological, same-market TimesFM3 vs full KalshiEdge refit research backtest."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "research" / "timesfm3_walkforward.py"


def load():
    spec = importlib.util.spec_from_file_location("timesfm3_walkforward", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def markets(n=200):
    return tuple((f"m{i:04}", 1_800_000_000_000_000_000 + i*900_000_000_000,
                  1_800_000_000_000_000_000 + i*900_000_000_000 + 820_000_000_000)
                 for i in range(n))


def test_three_expanding_folds_purge_and_holdout_groups_disjoint():
    m = load()
    plans = m.chronological_folds(
        markets(), train_markets=20, calibration_markets=8,
        test_markets=10, folds=3, embargo_markets=1)
    assert len(plans) == 3
    test_ids = []
    for i, p in enumerate(plans):
        assert len(p["train"]) == 20 + i*19
        assert len(p["calibration"]) == 8
        assert len(p["embargo"]) == 1
        assert len(p["test"]) == 10
        train, cal, gap, future = [set(p[key]) for key in
                                   ("train", "calibration", "embargo", "test")]
        assert not (train & cal or train & future or cal & future or gap & future)
        assert p["train_end_ns"] < p["calibration_start_ns"]
        assert p["calibration_end_ns"] < p["test_start_ns"]
        assert p["test_end_ns"] < 1_800_000_000_000_000_000 + 200*900_000_000_000
        test_ids.extend(p["test"])
        if i:
            assert set(plans[i-1]["test"]).issubset(train)
    assert len(set(test_ids)) == 30
    assert test_ids == sorted(test_ids)


def test_folds_reject_tiny_history_and_overlapping_market_time():
    m = load()
    with pytest.raises(ValueError, match="insufficient"):
        m.chronological_folds(markets(30), train_markets=20,
                              calibration_markets=8, test_markets=10, folds=3)
    invalid = list(markets(100))
    invalid[-2] = (invalid[-2][0], invalid[-2][1], invalid[-1][1] + 1)
    with pytest.raises(ValueError, match="overlap|chronological"):
        m.chronological_folds(invalid, train_markets=10,
                              calibration_markets=5, test_markets=6, folds=2)


def _prepared():
    # 8 markets, 2 folds, 4 per distinct date; refit model is different
    # from the already promoted incumbent weights.
    base=1_800_000_000_000_000_000
    records = []
    for i in range(8):
        records.append({
            "ticker":f"t{i}",
            "fold":i//4,
            "market_date":f"2026-11-{1+i//4:02d}",
            "checkpoint_ts_ns":base+i*900_000_000_000,
            "label_yes":i%2,
            "strike":100.0,
            "strike_type":"greater",
            "context":[99.0, 100.0],
            "kalshiedge_refit_p_yes": .75 if i%3 else .25,
        })
    return {"experiment_kind":"TIMESFM3_CHRONOLOGICAL_REFIT",
            "target_seconds":480,"records":records,
            "folds":[
                {"fold":0,"train_end_ns":100,"calibration_start_ns":101,
                 "calibration_end_ns":200,"test_start_ns":201,"test_end_ns":300,
                 "train_market_count":20,"test_market_count":4,
                 "test":[f"t{i}" for i in range(4)]},
                {"fold":1,"train_end_ns":301,"calibration_start_ns":302,
                 "calibration_end_ns":400,"test_start_ns":401,"test_end_ns":500,
                 "train_market_count":30,"test_market_count":4,
                 "test":[f"t{i}" for i in range(4,8)]},
            ],
            "sealed_current_lockbox_opened":False,
            "dataset_sha256":"a"*64,
            "input_sha256":"b"*64,
           }


def test_matched_walkforward_score_not_claiming_incumbent_model1_win():
    m=load()
    prep=_prepared()
    prediction={"checkpoint":"google/timesfm-3.0-pytorch",
                "input_sha256":"b"*64,
                "results":[{"ticker":f"t{i}","forecast_price":101. if i%4 else 99.}
                            for i in range(8)]}
    out=m.score_walkforward(prep,prediction,bootstrap_reps=80)
    assert out["markets"]==8
    assert out["fold_count"]==2
    assert out["model_name"]=="KalshiEdge full-pipeline refit"
    assert out["model1_deployed_weights_compared"] is False
    assert out["promotion_eligible"] is False
    assert out["winner"] is None
    assert out["timesfm3_brier"] is None
    assert out["timesfm3_69_gate_qualified"] is None
    assert out["kalshiedge_refit_brier"] is not None
    assert 0<=out["kalshiedge_refit_69_gate_coverage"]<=1
    assert out["paired_date_block_ci_95"]["low_pp"] <= out["paired_date_block_ci_95"]["high_pp"]
    assert len(out["per_fold"])==2
    assert out==m.score_walkforward(prep,prediction,bootstrap_reps=80)


def test_score_rejects_missing_duplicate_tampered_outcomes_and_extra_tickers():
    m=load()
    prep=_prepared()
    valid=[{"ticker":f"t{i}","forecast_price":101.} for i in range(8)]
    for results in (valid[:-1], valid + [valid[0]], valid+[{"ticker":"unknown","forecast_price":101.}],
                    valid[:3]+[{"ticker":"t3","forecast_price":float("nan")}] + valid[4:]):
        with pytest.raises(ValueError):
            m.score_walkforward(prep,{"checkpoint":"google/timesfm-3.0-pytorch",
                                      "input_sha256":"b"*64,"results":results})
    with pytest.raises(ValueError, match="hash"):
        m.score_walkforward(prep,{"checkpoint":"google/timesfm-3.0-pytorch",
                                  "input_sha256":"c"*64,"results":valid})


def test_score_rejects_overlapping_or_nonchronological_test_fold_metadata():
    m=load()
    prep=_prepared()
    prep["folds"][0]["calibration_end_ns"]=210
    prep["folds"][0]["test_start_ns"]=200
    with pytest.raises(ValueError, match="chronolog"):
        m.score_walkforward(prep,{"checkpoint":"google/timesfm-3.0-pytorch",
                                  "input_sha256":"b"*64,
                                  "results":[{"ticker":f"t{i}","forecast_price":101.} for i in range(8)]})


def test_normal_mode_cannot_use_new_unused_lockbox_when_preparing():
    m=load()
    groups=markets(200)
    allowed={x[0] for x in groups[:120]}
    plans=m.chronological_folds(
        [x for x in groups if x[0] in allowed],train_markets=20,
        calibration_markets=6,test_markets=10,folds=2)
    assert all(set(p["test"]).issubset(allowed) for p in plans)
    assert all(x not in allowed for x in plans[0]["test"] if x.startswith("zzz"))
