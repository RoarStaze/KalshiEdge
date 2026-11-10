"""Train Now builds a sealed dataset, audits it, and fits only that evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import forward_learning_480, training_cycle, dataset


def test_forward_training_snapshot_is_hash_sealed_and_reusable(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap.types import FeatureRow
    rows=[]
    for i in range(120):
        ts=1_900_000_000_000_000_000+i*900_000_000_000
        y=int(i%2==0)
        features={
            "seconds_remaining":480.0,"btc_normalized_distance":2.0 if y else -2.0,
            "btc_realized_vol_60s":.0003,"btc_realized_vol_300s":.0004,
            "btc_return_60s":.001 if y else -.001,"btc_return_60s_available":1.,
            "kalshi_quote_available":1.,"kalshi_yes_bid":.60 if y else .30,
            "kalshi_yes_ask":.64 if y else .34,"kalshi_mid":.62 if y else .32,
            "kalshi_spread":.04,"kalshi_quote_staleness_seconds":1.,
        }
        rows.append(FeatureRow(
            market_ticker=f"KXBTC15M-SEALED-{i}",split_group_id=f"KXBTC15M-SEALED-{i}",
            checkpoint_ts_ns=ts,label_yes=y,features=features,
            source_max_ts_ns={"binance":ts-1_000_000_000,
                              "kalshi_candles":ts-2_000_000_000},
        ))
    monkeypatch.setattr(forward_learning_480,"_joined",
                        lambda root:(rows,{"settled_480s":120}))
    monkeypatch.setattr(forward_learning_480,"_forecasts",
                        lambda root:())
    path, report=forward_learning_480.create_verified_forward_dataset(tmp_path)
    assert path.exists()
    assert report["ready"] is True
    restored,incumbents=forward_learning_480.load_verified_forward_dataset(path)
    assert len(restored)==120
    assert len(incumbents)==0
    blob=json.loads(path.read_text())
    blob["rows"][0]["label_yes"]=1-blob["rows"][0]["label_yes"]
    path.write_text(json.dumps(blob))
    with pytest.raises(ValueError,match="hash|integrity"):
        forward_learning_480.load_verified_forward_dataset(path)


def test_full_model_worker_builds_validated_dataset_before_training(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import evaluate
    monkeypatch.setattr(training_cycle,"inspect_baseline_training_readiness",
                        lambda root:{"ready":True,"reason":"verified lockbox"})
    # The raw source is available, so full model rebuild must actually execute.
    (tmp_path/"raw"/"kalshi"/"markets").mkdir(parents=True)
    events=[]
    def fake_build(root,settings,*,progress_callback=None):
        events.append("build")
        if progress_callback: progress_callback(10,10,"last-market","markets")
        return SimpleNamespace(market_count=10,row_count=480)
    monkeypatch.setattr(dataset,"build_dataset",fake_build)
    monkeypatch.setattr(training_cycle.training,"_verified_context",
                        lambda root:events.append("audit") or SimpleNamespace())
    monkeypatch.setattr(evaluate,"train_experiment",
                        lambda *args,**kwargs:events.append("train") or SimpleNamespace(
                            experiment_bundle_sha256="b"*64,
                            experiment_path=tmp_path/"experiments"/"b.json",
                        ))
    monkeypatch.setattr(evaluate,"run_lockbox_evaluation",
                        lambda *args,**kwargs:events.append("evaluate") or SimpleNamespace(
                            decision=SimpleNamespace(promoted=False,reason_codes=("NOT_BEATEN",)),
                            report_path=tmp_path/"evaluation.json",
                        ))
    assert training_cycle.run_training_worker(tmp_path,mode="baseline",
                                               round_number=0,git_sha="a"*40)==0
    assert events.index("build")<events.index("audit")<events.index("train")<events.index("evaluate")
    current=training_cycle._run_state(tmp_path)
    assert current["decision"]=="VALIDATION_FAILED"
    assert "BUILDING_DATASET" in [x["stage"] for x in current["events"]]
    assert "VERIFYING_DATASET" in [x["stage"] for x in current["events"]]


def test_train_now_page_shows_full_seven_stages_and_model_index():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert '"BUILDING_DATASET","Create dataset"' in PAGE
    assert '"VERIFYING_DATASET","Audit dataset"' in PAGE
    assert '"FITTING","Fit model"' in PAGE
    assert 'id="active-model-number"' in PAGE
    assert 'id="installed-model-number"' in PAGE
    assert 'id="dataset-build-detail"' in PAGE
