"""CUDA must be used only when verified, without changing any forecast path."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import cuda_training, evaluate, structural, training
from kalshi_edge.bootstrap.types import FeatureRow


def _row(index=0):
    return FeatureRow(
        market_ticker=f"TICKER-{index}", split_group_id=f"TICKER-{index}",
        market_date="2026-11-14",
        checkpoint_ts_ns=1_800_000_000_000_000_000 + index*900_000_000_000,
        label_yes=index%2,
        features={
            "btc_close":100_000.0, "strike":100_100.0,
            "seconds_remaining":480.0, "btc_realized_vol_60s":0.0004,
            "btc_return_5s":0.00001, "btc_return_5s_available":1.0,
        },
        source_max_ts_ns={},
    )


def _model():
    return structural.StructuralModel(
        candidate="diffusion", seed=73115, simulations=8,
        drift_per_second=0.000001, drift_cap_per_second=0.000005,
        residuals=(-1.0,0.0,1.0),
    )


def test_xgboost_can_use_verified_cuda_even_without_cupy(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU","auto")
    monkeypatch.setattr(cuda_training, "_cuda_hardware_ready",
                        lambda: (_ for _ in ()).throw(AssertionError("CuPy check must not gate XGBoost")))
    monkeypatch.setattr(cuda_training,"_xgboost_cuda_build",lambda:True)
    monkeypatch.setattr(cuda_training,"_xgboost_cuda_runtime_ready",lambda:True)
    assert cuda_training.xgboost_device() == "cuda"


def test_xgboost_with_missing_cuda_falls_back_or_fails_closed(monkeypatch):
    monkeypatch.setattr(cuda_training,"_xgboost_cuda_build",lambda:True)
    monkeypatch.setattr(cuda_training,"_xgboost_cuda_runtime_ready",lambda:False)
    monkeypatch.setenv("KALSHI_TRAIN_GPU","auto")
    assert cuda_training.xgboost_device()=="cpu"
    monkeypatch.setenv("KALSHI_TRAIN_GPU","on")
    with pytest.raises(cuda_training.CUDATrainingError,match="CUDA"):
        cuda_training.xgboost_device()


def test_structural_scores_forward_verified_gpu_and_replay_cpu_identically():
    model=_model()
    rows=[_row(i) for i in range(8)]
    expected=[model.predict_proba(structural.structural_state_from_row(row)) for row in rows]
    class GPU:
        def __init__(self):
            self.calls=0
        def predict_probabilities(self, fitted, states, *, progress=None, chunk_size=32):
            assert fitted is model
            self.calls+=1
            return [fitted.predict_proba(s) for s in states]
    gpu=GPU()
    assert training._structural_scores(model,rows,gpu_backend=gpu)==expected
    assert gpu.calls==1
    assert training._structural_scores(model,rows)==expected


def test_holdout_gpu_selector_caches_backend_and_skips_unused_incumbent(monkeypatch):
    from kalshi_edge.bootstrap import cuda_training
    calls=[]
    backend=object()
    def choose(model,states):
        calls.append((model,tuple(states)))
        assert len(states)==24
        return backend
    monkeypatch.setattr(cuda_training,"choose_structural_gpu_backend",choose)
    bundle=SimpleNamespace(bundle_sha256="abc"*21+"d")
    pipeline=SimpleNamespace(
        structural_model=_model(),
        stacker=SimpleNamespace(weights={"structural":0.0}),
    )
    rows=tuple(_row(i) for i in range(40))
    cache={}
    assert evaluate._holdout_gpu_backend(bundle,pipeline,rows,cache,candidate_only=True) is None
    assert calls==[]
    assert evaluate._holdout_gpu_backend(bundle,pipeline,rows,cache) is backend
    assert evaluate._holdout_gpu_backend(bundle,pipeline,rows[:30],cache) is backend
    assert len(calls)==1


def test_holdout_predict_and_candidate_forward_backend(monkeypatch):
    selected=object()
    capture=[]
    class Pipeline:
        structural_model=_model()
        stacker=SimpleNamespace(weights={"structural":0.3})
        def predict(self,rows,*,structural_executor=None,structural_progress=None,structural_gpu_backend=None):
            capture.append(("diagnostic",structural_gpu_backend))
            return {"candidate":[0.4]*len(rows)}
        def base_candidate(self,rows,*,structural_executor=None,structural_progress=None,structural_gpu_backend=None):
            capture.append(("candidate",structural_gpu_backend))
            return [0.4]*len(rows)
    monkeypatch.setattr(evaluate,"_verified_pipeline",lambda bundle:Pipeline())
    monkeypatch.setattr(cuda_training,"choose_structural_gpu_backend",lambda model,states:selected)
    bundle=SimpleNamespace(bundle_sha256="a"*64)
    rows=[_row(i) for i in range(24)]
    cache={}
    assert evaluate._predict_bundle_rows(bundle,rows,gpu_backends=cache)["candidate"] == [0.4]*24
    assert evaluate._predict_bundle_candidate(bundle,rows,gpu_backends=cache)==[0.4]*24
    assert capture==[("diagnostic",selected),("candidate",selected)]


def test_holdout_gpu_selector_does_not_cache_failed_strict_validation(monkeypatch):
    def invalid(model,states):
        raise cuda_training.CUDATrainingError("GPU forced but unavailable")
    monkeypatch.setattr(cuda_training,"choose_structural_gpu_backend",invalid)
    pipeline=SimpleNamespace(structural_model=_model(),stacker=SimpleNamespace(weights={"structural":1.0}))
    bundle=SimpleNamespace(bundle_sha256="b"*64)
    cache={}
    with pytest.raises(cuda_training.CUDATrainingError,match="forced"):
        evaluate._holdout_gpu_backend(bundle,pipeline,[_row(i) for i in range(24)],cache)
    assert cache=={}


def test_calibration_structural_scores_use_gpu_or_safe_cpu(monkeypatch):
    selected=object()
    calls=[]
    monkeypatch.setattr(training._structural_worker_count.__globals__["os"],"cpu_count",lambda:1)
    monkeypatch.setattr(training,"_structural_worker_count",lambda:1)
    def scoring(model,rows,*,executor=None,progress=None,gpu_backend=None):
        calls.append(gpu_backend)
        return [0.6]*len(rows)
    monkeypatch.setattr(training,"_structural_scores",scoring)
    monkeypatch.setattr(cuda_training,"choose_structural_gpu_backend",lambda model,states:selected)
    rows=[_row(i) for i in range(24)]
    assert training._calibration_structural_scores(_model(),rows)==[0.6]*24
    assert calls==[selected]
    monkeypatch.setattr(cuda_training,"choose_structural_gpu_backend",lambda model,states:None)
    assert training._calibration_structural_scores(_model(),rows)==[0.6]*24
    assert calls[-1] is None
