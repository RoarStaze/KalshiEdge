"""CUDA training must be real, measurable, optional, and numerically auditable."""
from __future__ import annotations

import importlib.util
import random
import sys

import pytest

from kalshi_edge.bootstrap import structural, training, models
from kalshi_edge.bootstrap import cuda_training


def _reference_model(candidate="diffusion"):
    return structural.StructuralModel(
        candidate=candidate, seed=73115, simulations=24,
        drift_per_second=0.00001, drift_cap_per_second=0.0001,
        residuals=(-1.5, -0.25, 0.5, 1.0),
    )


def _reference_states():
    return tuple(
        structural.StructuralState(
            current_value=100_000 + 16 * i,
            strike=100_020, seconds_remaining=65 + (i % 3),
            volatility_per_second=0.0006, recent_return_5s=0.00002,
        ) for i in range(12)
    )


@pytest.mark.parametrize("candidate", ["diffusion", "empirical_residual"])
def test_gpu_shocks_are_the_exact_original_cpu_random_stream(candidate):
    model = _reference_model(candidate)
    steps = 65
    shocks = cuda_training.seeded_shocks(model, steps)
    rng = random.Random(model.seed)
    assert len(shocks) == model.simulations * steps
    assert shocks == [model._shock(rng) for _ in range(model.simulations * steps)]


def test_gpu_backend_off_does_not_import_cuda(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "off")
    monkeypatch.setattr(cuda_training, "load_cupy", lambda:
                        (_ for _ in ()).throw(AssertionError("CuPy must not load")))
    assert cuda_training.choose_structural_gpu_backend(_reference_model(), _reference_states()) is None
    assert cuda_training.xgboost_device() == "cpu"


def test_auto_falls_back_cleanly_if_cuda_dependency_is_missing(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "auto")
    monkeypatch.setattr(cuda_training, "load_cupy", lambda: (_ for _ in ()).throw(ImportError("no cupy")))
    assert cuda_training.choose_structural_gpu_backend(_reference_model(), _reference_states()) is None
    assert cuda_training.xgboost_device() == "cpu"


def test_explicit_cuda_on_fails_with_actionable_installation_error(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "on")
    monkeypatch.setattr(cuda_training, "load_cupy", lambda: (_ for _ in ()).throw(ImportError("no cupy")))
    with pytest.raises(cuda_training.CUDATrainingError, match="research-gpu"):
        cuda_training.choose_structural_gpu_backend(_reference_model(), _reference_states())


def test_cuda_selected_only_after_parity_and_positive_speed_benchmark(monkeypatch):
    model = _reference_model()
    states = _reference_states()
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "auto")
    fake_backend = object()
    monkeypatch.setattr(cuda_training, "_create_backend", lambda: fake_backend)
    monkeypatch.setattr(cuda_training, "_verify_backend",
                        lambda backend, model, states: {"matched": True, "gpu_seconds": 1.0, "cpu_seconds": 4.0})
    assert cuda_training.choose_structural_gpu_backend(model, states) is fake_backend
    monkeypatch.setattr(cuda_training, "_verify_backend",
                        lambda backend, model, states: {"matched": True, "gpu_seconds": 8.0, "cpu_seconds": 4.0})
    assert cuda_training.choose_structural_gpu_backend(model, states) is None
    monkeypatch.setattr(cuda_training, "_verify_backend",
                        lambda backend, model, states: {"matched": False, "gpu_seconds": 1.0, "cpu_seconds": 4.0})
    assert cuda_training.choose_structural_gpu_backend(model, states) is None


def test_xgboost_uses_cuda_only_with_eligible_build_and_device(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "auto")
    monkeypatch.setattr(cuda_training, "_cuda_hardware_ready", lambda: True)
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_build", lambda: True)
    assert cuda_training.xgboost_device() == "cuda"
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_build", lambda: False)
    assert cuda_training.xgboost_device() == "cpu"
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_build", lambda: True)
    monkeypatch.setattr(cuda_training, "_cuda_hardware_ready", lambda: False)
    assert cuda_training.xgboost_device() == "cpu"


def test_xgboost_cuda_fail_closed_if_user_explicitly_requests_gpu(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "on")
    monkeypatch.setattr(cuda_training, "_cuda_hardware_ready", lambda: False)
    with pytest.raises(cuda_training.CUDATrainingError, match="CUDA"):
        cuda_training.xgboost_device()


def test_original_cpu_structural_output_unchanged_when_gpu_is_disabled(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "off")
    model = _reference_model("empirical_residual")
    states = _reference_states()
    baseline = [model.predict_proba(state) for state in states]
    assert structural.predict_probabilities(model, states) == baseline
    assert cuda_training.choose_structural_gpu_backend(model, states) is None


@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="optional CUDA wheel not installed in CPU CI")
@pytest.mark.parametrize("candidate", ["diffusion", "empirical_residual"])
def test_real_cuda_kernel_matches_original_cpu_monte_carlo(candidate):
    if not cuda_training.cuda_device_available():
        pytest.skip("no NVIDIA CUDA device")
    model = _reference_model(candidate)
    states = _reference_states()
    backend = cuda_training.CUDAStructuralBackend(cuda_training.load_cupy())
    assert backend.predict_probabilities(model, states) == [
        model.predict_proba(state) for state in states
    ]
