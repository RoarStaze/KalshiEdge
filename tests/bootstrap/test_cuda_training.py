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
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_runtime_ready", lambda: True)
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


class _CPUEmulatedKernel:
    """Run the exact CUDA kernel interface with CPU math to validate wiring."""
    def compile(self):
        return None

    def __call__(self, grid, blocks, args):
        import math
        shocks, starts, drifts, sigmas, averages, steps, simulations, count = args
        for row in range(int(count)):
            for simulation in range(int(simulations)):
                value = float(starts[row])
                total = 0.0
                for step in range(int(steps)):
                    value *= math.exp(
                        float(drifts[row])
                        + float(sigmas[row]) * float(shocks[simulation * int(steps) + step])
                    )
                    if step >= int(steps) - 60:
                        total += value
                averages[row * int(simulations) + simulation] = total / 60.0


class _CPUEmulatedCuPy:
    """CI lacks NVIDIA GPU; this exercises batching and exact shock stream."""
    import numpy as np
    float64 = np.float64

    def RawKernel(self, source, name):
        assert "structural_paths" in source
        return _CPUEmulatedKernel()

    def asarray(self, values, *, dtype):
        return self.np.asarray(values, dtype=dtype)

    def empty(self, n, *, dtype):
        return self.np.empty(n, dtype=dtype)

    def asnumpy(self, values):
        return values


@pytest.mark.parametrize("candidate", ["diffusion", "empirical_residual"])
def test_cuda_kernel_interface_replays_cpu_probabilities_with_seeded_shocks(candidate):
    backend = cuda_training.CUDAStructuralBackend(_CPUEmulatedCuPy())
    model = _reference_model(candidate)
    states = _reference_states()
    notices = []
    got = backend.predict_probabilities(
        model, states, progress=lambda done, total: notices.append((done, total)),
        chunk_size=3,
    )
    expected = [model.predict_proba(state) for state in states]
    assert got == expected
    assert notices[-1] == (len(states), len(states))
    assert len(notices) >= 4


def test_structural_predict_probabilities_uses_verified_gpu_backend():
    backend = _CPUEmulatedCuPy()
    gpu = cuda_training.CUDAStructuralBackend(backend)
    model = _reference_model()
    states = _reference_states()
    assert structural.predict_probabilities(model, states, gpu_backend=gpu) == [
        model.predict_proba(state) for state in states
    ]


def test_gpu_invalid_numerics_replay_entire_original_cpu_state():
    class BadKernel(_CPUEmulatedKernel):
        def __call__(self, grid, block, args):
            args[4][:] = float("nan")
    class BadCuPy(_CPUEmulatedCuPy):
        def RawKernel(self, source, name):
            return BadKernel()
    backend = cuda_training.CUDAStructuralBackend(BadCuPy())
    model = _reference_model()
    states = _reference_states()
    assert backend.predict_probabilities(model, states) == [
        model.predict_proba(state) for state in states
    ]


def test_gpu_cuda_runtime_error_replays_cpu_batch_in_auto_mode(monkeypatch):
    class FailedBackend:
        def predict_probabilities(self, *args, **kwargs):
            raise RuntimeError("device reset")
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "auto")
    model = _reference_model()
    states = _reference_states()
    assert structural.predict_probabilities(model, states, gpu_backend=FailedBackend()) == [
        model.predict_proba(state) for state in states
    ]


def test_gpu_cuda_runtime_error_is_exposed_when_gpu_is_forced(monkeypatch):
    class FailedBackend:
        def predict_probabilities(self, *args, **kwargs):
            raise RuntimeError("device reset")
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "on")
    with pytest.raises(RuntimeError, match="device reset"):
        structural.predict_probabilities(
            _reference_model(), _reference_states(), gpu_backend=FailedBackend(),
        )


def test_gpu_xgboost_training_serializes_cpu_portable_estimator(monkeypatch):
    """CUDA fit must not require CUDA for later live-forecast inference."""
    from types import SimpleNamespace

    constructed = []
    class FakeXGBClassifier:
        def __init__(self, **kwargs):
            self.initial_device = kwargs["device"]
            self.device = kwargs["device"]
            constructed.append(self)
        def fit(self, x, y):
            return self
        def set_params(self, **kwargs):
            self.device = kwargs["device"]
            return self

    monkeypatch.setitem(sys.modules, "xgboost", SimpleNamespace(
        XGBClassifier=FakeXGBClassifier,
    ))
    monkeypatch.setattr(cuda_training, "xgboost_device", lambda: "cuda")
    fitted = models._fit_candidate(
        "xgboost", {"max_depth": 2, "learning_rate": 0.05},
        [[0.0], [1.0], [0.2], [0.8]], [0, 1, 0, 1], 123,
    )
    assert constructed[0].initial_device == "cuda"
    assert fitted.device == "cpu"


def test_xgboost_cuda_runtime_probe_can_prevent_false_gpu_reporting(monkeypatch):
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "auto")
    monkeypatch.setattr(cuda_training, "_cuda_hardware_ready", lambda: True)
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_build", lambda: True)
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_runtime_ready", lambda: False)
    assert cuda_training.xgboost_device() == "cpu"

    monkeypatch.setenv("KALSHI_TRAIN_GPU", "on")
    with pytest.raises(cuda_training.CUDATrainingError, match="CUDA"):
        cuda_training.xgboost_device()


def test_cuda_self_check_reports_cpu_fallback_without_gpu(monkeypatch, capsys):
    import json
    monkeypatch.setenv("KALSHI_TRAIN_GPU", "off")
    monkeypatch.setattr(cuda_training, "cuda_device_available", lambda: False)
    monkeypatch.setattr(cuda_training, "_xgboost_cuda_build", lambda: False)
    monkeypatch.setattr(sys, "argv", ["cuda_training", "--check"])
    assert cuda_training.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cuda_available"] is False
    assert report["structural_backend"] == "cpu"
    assert report["xgboost_backend"] == "cpu"
    assert report["mode"] == "off"
