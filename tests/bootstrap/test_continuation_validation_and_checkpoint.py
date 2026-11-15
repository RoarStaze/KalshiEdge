"""A failed 63% continuation must save real fitted evidence and NOT switch live model."""
from __future__ import annotations

import base64
import hashlib
import json
import pickle
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import artifact, evaluate, model_continuation as cont
from kalshi_edge.bootstrap import model_continuation_run, live, training
from kalshi_edge.bootstrap.types import FeatureRow


class TinyFrozenPipeline:
    required_feature_names=("kalshi_mid","seconds_remaining")
    outcome_feature_names=("kalshi_mid",)
    structural_model="unchanged-structural"
    calibrator="unchanged-calibrator"
    stacker="unchanged-stacker"

    def predict(self, rows, *, structural_executor=None):
        # This mock is independent of future labels and never fits.
        return {"candidate":[.85 if r.features["kalshi_mid"]>.5 else .15 for r in rows]}


def bundle():
    payload=pickle.dumps(TinyFrozenPipeline(),protocol=5)
    return artifact.ModelBundle(
        stage="promoted",
        model_version=training.MODEL_VERSION,
        git_sha="a"*40,random_seed=2026,
        feature_schema_version=training.FEATURE_SCHEMA_VERSION,
        feature_names=("kalshi_mid","seconds_remaining"),
        boundaries=artifact.SplitBoundaries(
            train_start_ts_ns=100,train_end_ts_ns=200,
            calibration_start_ts_ns=201,calibration_end_ts_ns=300,
            lockbox_start_ts_ns=301,lockbox_end_ts_ns=400,
        ),
        input_hashes={"derived/features.parquet":"b"*64},
        training_config_hash="c"*64,library_versions={},
        components=(artifact.ComponentIdentity(
            name="kalshi_prior",kind="benchmark",identity="mid",weight=1.0,
        ),),
        calibration_method="identity",
        leakage_audit=artifact.LeakageEvidence(
            passed=True,finding_count=0,finding_codes=(),
        ),
        metrics={},ablations=(artifact.AblationEvidence(
            name="test-inherited",retained=True,reason="inherited",
            full_metrics=artifact.MetricSnapshot(
                accuracy=.7,brier=.18,log_loss=.53,ece=.03,sharpness=.15,
            ),
            ablated_metrics=artifact.MetricSnapshot(
                accuracy=.6,brier=.2,log_loss=.6,ece=.04,sharpness=.15,
            ),
        ),),
        serialized_pipeline_b64=base64.b64encode(payload).decode(),
        serialized_pipeline_sha256=hashlib.sha256(payload).hexdigest(),
        source_experiment_sha256="e"*64,
        promotion_rule=evaluate.LEGACY_PROMOTION_RULE_ID,
    )


def pairs(n=130):
    out=[]
    for i in range(n):
        ts=900000000000000000+i*900000000000
        prior=.75 if i%2 else .25
        row=FeatureRow(
            market_ticker=f"KXBTC15M-CONTINUE-{i}",
            split_group_id=f"KXBTC15M-CONTINUE-{i}",
            checkpoint_ts_ns=ts,label_yes=i%2,
            features={"kalshi_mid":prior,"seconds_remaining":480.0},
            source_max_ts_ns={"binance":ts-1000},
        )
        out.append((row,SimpleNamespace(
            market_ticker=row.market_ticker,prediction_ts_ns=ts,
            checkpoint_seconds=480,model_hash="ignored",
            p_yes=.80 if i%2 else .20,
            coin_flip_assessment="LOW",
        )))
    return out


def test_full_continuation_flow_retains_sha_sealed_checkpoint_after_rejection(tmp_path,monkeypatch):
    root=tmp_path.resolve()
    p=artifact.save_model_bundle(bundle(),root/"models"/"promoted")
    active=artifact.load_model_bundle(p)
    pointer=root/"models"/"default.json"
    evaluate._atomic_json(pointer,{"model_number":2,"previous_model_hash":"b"*64,
        "bundle_sha256":active.bundle_sha256,
        "path":p.relative_to(root).as_posix(),
        "source_experiment_sha256":"e"*64,
        "promotion_rule":active.promotion_rule})
    before=pointer.read_bytes()
    src=pairs()
    monkeypatch.setattr(live,"load_default_model",
                        lambda root:(active,TinyFrozenPipeline()))
    monkeypatch.setattr(live,"load_default_bundle",lambda root:active)
    monkeypatch.setattr(cont,"source_pairs",
                        lambda root,*,active_hash,required_features:(src,400,{"settled_480s":130}))
    monkeypatch.setattr(cont,"continue_pipeline",
                        lambda pipeline,development:TinyFrozenPipeline())
    calls=[]
    def finalize(root,experiment_path,report):
        assert report.requires_retrain_480_gate
        assert report.decision_metrics["retrain_480_candidate"].total_markets==80
        assert report.decision_metrics["retrain_480_candidate"].minimum_confidence==.63
        assert report.dataset_sha256 != active.input_hashes["derived/features.parquet"]
        calls.append((experiment_path, report))
        return SimpleNamespace(
            decision=SimpleNamespace(promoted=False,
                                     reason_codes=("RETRAIN_480_COVERAGE_NOT_INCREASED",)),
            report_path=root/"reports"/"saved-failure.json",
        )
    monkeypatch.setattr(evaluate,"_finalize_evaluation",finalize)
    result=model_continuation_run.run_continuation(root,git_sha="f"*40)
    assert result["status"]=="TRAINED_NOT_PROMOTED"
    assert result["retrained_entire_model"] is False
    assert result["continued_classifier_components"] is True
    assert result["development_markets"]==40
    assert result["validation_markets"]==80
    assert pointer.read_bytes()==before
    checkpoint=json.loads((root/"models"/"continuation"/"latest.json").read_text())
    assert checkpoint["active_parent_sha256"]==active.bundle_sha256
    fit=artifact.load_model_bundle(calls[0][0])
    assert fit.stage=="experiment"
    assert fit.serialized_pipeline_sha256 != active.serialized_pipeline_sha256 or fit.training_config_hash != active.training_config_hash
    assert fit.input_hashes["continuation/active_parent_bundle"]==active.bundle_sha256


def test_continuation_refuses_reusing_insufficient_fresh_market_sample(tmp_path,monkeypatch):
    root=tmp_path.resolve()
    p=artifact.save_model_bundle(bundle(),root/"models"/"promoted")
    active=artifact.load_model_bundle(p)
    monkeypatch.setattr(live,"load_default_model",
                        lambda root:(active,TinyFrozenPipeline()))
    monkeypatch.setattr(cont,"source_pairs",
                        lambda root,*,active_hash,required_features:(pairs(20),400,{}))
    with pytest.raises(cont.ContinuationError, match="20/130"):
        model_continuation_run.run_continuation(root,git_sha="f"*40)
    assert not (root/"models"/"continuation"/"latest.json").exists()
