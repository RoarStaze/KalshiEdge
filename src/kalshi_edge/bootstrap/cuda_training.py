"""Optional NVIDIA CUDA acceleration for research training only.

There is NO CuPy/CUDA import at package import time. Standard CPU research,
production forecasts and the sealed lockbox remain unchanged.

Structural GPU execution replays the *same* Python seeded shock draws used by
StructuralModel.predict_proba(). The GPU accelerates double-precision path
propagation across independent simulations and historical rows. Ambiguous
near-strike results are recomputed on CPU, so CUDA numerical rounding cannot
silently decide a close contract.
"""
from __future__ import annotations

import json
import logging
import math
import os
from functools import lru_cache
import random
import time
from typing import Any, Callable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .structural import StructuralModel, StructuralState

LOG = logging.getLogger(__name__)


class CUDATrainingError(RuntimeError):
    """A requested GPU research backend is not available or did not verify."""


CUDA_KERNEL = r'''
extern "C" __global__ void structural_paths(
    const double* shocks, const double* starting,
    const double* drift, const double* sigma,
    double* averages, const int steps,
    const int simulations, const int count)
{
    const int i = blockDim.x * blockIdx.x + threadIdx.x;
    if (i >= count * simulations) return;
    const int row = i / simulations;
    const int simulation = i % simulations;
    double value = starting[row];
    double total = 0.0;
    const double mean_drift = drift[row];
    const double volatility = sigma[row];
    const int shock_offset = simulation * steps;
    for (int t = 0; t < steps; ++t) {
        value *= exp(mean_drift + volatility * shocks[shock_offset + t]);
        if (t >= steps - 60) total += value;
    }
    averages[i] = total / 60.0;
}
'''


def gpu_mode() -> str:
    value = os.environ.get("KALSHI_TRAIN_GPU", "auto").strip().lower()
    if value not in ("auto", "on", "off"):
        raise CUDATrainingError("KALSHI_TRAIN_GPU must be auto, on, or off")
    return value


def load_cupy() -> Any:
    import cupy  # noqa: PLC0415 - optional GPU dependency
    return cupy


def _cuda_hardware_ready() -> bool:
    try:
        cp = load_cupy()
        if cp.cuda.runtime.getDeviceCount() < 1:
            return False
        free, total = cp.cuda.runtime.memGetInfo()
        # Laptop also runs display, games and potentially live trading.
        return free >= 512 * 1024 * 1024 and total >= 1024 * 1024 * 1024
    except (ImportError, OSError, RuntimeError, ValueError):
        return False


def cuda_device_available() -> bool:
    return _cuda_hardware_ready()


def _xgboost_cuda_build() -> bool:
    try:
        import xgboost
        enabled = xgboost.build_info().get("USE_CUDA")
        return enabled is True or str(enabled).strip().upper() in ("1", "ON", "TRUE", "YES")
    except (ImportError, AttributeError, RuntimeError):
        return False


@lru_cache(maxsize=1)
def _xgboost_cuda_runtime_ready() -> bool:
    """A CUDA-enabled wheel is insufficient: confirm a real GPU fit occurred."""
    if not _xgboost_cuda_build():
        return False
    try:
        import warnings
        import numpy as np
        from xgboost import XGBClassifier

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            probe = XGBClassifier(
                n_estimators=2, max_depth=1, learning_rate=0.1,
                tree_method="hist", device="cuda", n_jobs=1,
                objective="binary:logistic",
            ).fit(
                np.asarray([[0.0], [1.0], [0.2], [0.8]], dtype=np.float32),
                np.asarray([0, 1, 0, 1], dtype=np.int32),
            )
        configuration = json.loads(probe.get_booster().save_config())
        device = str(configuration["learner"]["generic_param"]["device"]).lower()
        return device.startswith("cuda")
    except Exception as exc:
        LOG.warning("XGBoost CUDA smoke fit failed; CPU fallback: %s", exc)
        return False


def xgboost_device() -> str:
    """Select CUDA only if both runtime hardware and full GPU XGBoost exist."""
    mode = gpu_mode()
    if mode == "off":
        return "cpu"
    available = _cuda_hardware_ready()
    built = _xgboost_cuda_build() if available else False
    verified = _xgboost_cuda_runtime_ready() if built else False
    if available and built and verified:
        return "cuda"
    if mode == "on":
        raise CUDATrainingError(
            "CUDA XGBoost unavailable. Install KalshiEdge research-gpu "
            "dependencies and verify your NVIDIA driver/CUDA support."
        )
    return "cpu"


def seeded_shocks(model: "StructuralModel", steps: int) -> list[float]:
    """Exactly the row-major random stream used by the production CPU path."""
    if steps < 60 or model.simulations < 1:
        raise ValueError("invalid structural simulation dimensions")
    rng = random.Random(model.seed)
    return [model._shock(rng) for _ in range(model.simulations * steps)]


class CUDAStructuralBackend:
    """GPU path propagator using identical CPU-seeded shocks.

    This backend is *training only*. Production inference remains CPU,
    independent of whether CUDA was available when the model was fitted.
    """
    def __init__(self, cp: Any):
        self.cp = cp
        self.kernel = cp.RawKernel(CUDA_KERNEL, "structural_paths")
        # Compile early so that device/kernel errors cannot be mistaken for
        # completed training work. CuPy handles NVRTC caching.
        self.kernel.compile()

    def predict_probabilities(
        self,
        model: "StructuralModel",
        states: Sequence["StructuralState"],
        *,
        progress: Callable[[int, int], None] | None = None,
        chunk_size: int = 32,
    ) -> list[float]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        cp = self.cp
        result: list[float | None] = [None] * len(states)
        groups: dict[int, list[tuple[int, "StructuralState"]]] = {}
        for idx, state in enumerate(states):
            if state.seconds_remaining < 60.0:
                raise ValueError("pre-final-minute structural state is required")
            steps = max(60, math.ceil(state.seconds_remaining))
            if steps > 3600:
                raise CUDATrainingError("CUDA model path exceeds 3600 simulated seconds")
            groups.setdefault(steps, []).append((idx, state))

        finished = 0
        for steps, items in groups.items():
            # Compute once per model and remaining-time group, not once
            # per input row. CPU path repeats this SAME seeded stream for
            # every row, so reusing it is mathematically equivalent.
            shocks = cp.asarray(seeded_shocks(model, steps), dtype=cp.float64)
            for offset in range(0, len(items), chunk_size):
                piece = items[offset:offset + chunk_size]
                n = len(piece)
                starts = cp.asarray([state.current_value for _, state in piece],
                                    dtype=cp.float64)
                drifts = cp.asarray(
                    [model._effective_drift(state.recent_return_5s)
                     for _, state in piece],
                    dtype=cp.float64,
                )
                sigmas = cp.asarray([state.volatility_per_second for _, state in piece],
                                    dtype=cp.float64)
                averages = cp.empty(n * model.simulations, dtype=cp.float64)
                threads = 128
                blocks = (n * model.simulations + threads - 1) // threads
                # RawKernel C declarations are int32, not Python's
                # platform-dependent machine-sized integer scalars.
                from numpy import int32
                self.kernel(
                    (blocks,), (threads,),
                    (shocks, starts, drifts, sigmas, averages,
                     int32(steps), int32(model.simulations), int32(n)),
                )
                # Synchronizes GPU; expose ordinary host numeric outputs to
                # the normal scorer with no live CuPy GPU dependencies.
                host = cp.asnumpy(averages).reshape((n, model.simulations))
                for row_number, (index, state) in enumerate(piece):
                    samples = host[row_number]
                    # CUDA and CPU math.exp may differ in their final few
                    # floating-point bits. Resolve ANY near-strike path
                    # using the unchanged CPU routine, not GPU rounding.
                    boundary = max(1e-6, abs(state.strike) * 1e-7)
                    if not all(math.isfinite(float(value)) for value in samples):
                        probability = model.predict_proba(state)
                    elif any(abs(float(value) - state.strike) < boundary
                             for value in samples):
                        probability = model.predict_proba(state)
                    else:
                        hits = sum(float(value) >= state.strike for value in samples)
                        probability = hits / model.simulations
                    result[index] = probability
                finished += n
                if progress is not None:
                    progress(finished, len(states))
        if any(value is None for value in result):
            raise CUDATrainingError("CUDA structural evaluation left uncomputed rows")
        return [float(value) for value in result]


def _create_backend() -> CUDAStructuralBackend:
    cp = load_cupy()
    if not _cuda_hardware_ready():
        raise CUDATrainingError("No compatible CUDA GPU with sufficient free VRAM detected")
    return CUDAStructuralBackend(cp)


def _verify_backend(
    backend: CUDAStructuralBackend,
    model: "StructuralModel",
    states: Sequence["StructuralState"],
) -> dict[str, Any]:
    # Use representative real historical states; GPU may not run
    # faster on tiny batches, hence the 24-row performance check.
    sample = tuple(states[:24])
    if not sample:
        return {"matched": False, "gpu_seconds": 0.0, "cpu_seconds": 0.0}

    backend.predict_probabilities(model, sample[:1])  # compile/warm-up
    start = time.perf_counter()
    expected = [model.predict_proba(state) for state in sample]
    cpu_seconds = time.perf_counter() - start
    start = time.perf_counter()
    actual = backend.predict_probabilities(model, sample)
    gpu_seconds = time.perf_counter() - start
    return {
        "matched": actual == expected,
        "gpu_seconds": gpu_seconds,
        "cpu_seconds": cpu_seconds,
    }


def choose_structural_gpu_backend(
    model: "StructuralModel",
    states: Sequence["StructuralState"],
) -> CUDAStructuralBackend | None:
    """GPU default only when real CUDA parity AND speed evidence support it."""
    mode = gpu_mode()
    if mode == "off":
        return None
    try:
        backend = _create_backend()
        result = _verify_backend(backend, model, states)
        if result["matched"] is not True:
            raise CUDATrainingError(
                "CUDA structural Monte Carlo failed CPU probability parity"
            )
        cpu_seconds = float(result["cpu_seconds"])
        gpu_seconds = float(result["gpu_seconds"])
        if mode == "auto" and (cpu_seconds <= 0.0 or
                               gpu_seconds >= cpu_seconds * 0.95):
            LOG.info(
                "CUDA structural training not faster on verified benchmark "
                "(GPU %.3fs; CPU %.3fs); using multi-core CPU",
                gpu_seconds, cpu_seconds,
            )
            return None
        LOG.info(
            "Verified CUDA structural training parity; GPU %.3fs vs CPU %.3fs",
            gpu_seconds, cpu_seconds,
        )
        return backend
    except Exception as exc:
        if mode == "on":
            if isinstance(exc, CUDATrainingError):
                raise
            raise CUDATrainingError(
                "CUDA required but cannot initialize: install KalshiEdge "
                "research-gpu dependencies and check the NVIDIA driver: "
                + str(exc)
            ) from exc
        LOG.warning("CUDA unavailable or failed verification; using multi-core CPU: %s", exc)
        return None



def main() -> int:
    """Read-only on-device benchmark: python -m kalshi_edge.bootstrap.cuda_training --check."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="KalshiEdge NVIDIA CUDA training self-check")
    parser.add_argument("--check", action="store_true",
                        help="test CUDA initialization, parity and benchmark before training")
    args = parser.parse_args()
    if not args.check:
        parser.print_help()
        return 0

    from . import structural
    report: dict[str, Any] = {
        "mode": gpu_mode(),
        "cuda_available": cuda_device_available(),
        "xgboost_cuda_build": _xgboost_cuda_build(),
        "structural_backend": "cpu",
        "xgboost_backend": "cpu",
    }
    if report["cuda_available"]:
        try:
            cp = load_cupy()
            device = cp.cuda.runtime.getDeviceProperties(0).get("name", "NVIDIA CUDA GPU")
            report["gpu_name"] = (
                device.decode("utf-8", errors="replace")
                if isinstance(device, bytes) else str(device)
            )
        except Exception as exc:
            report["gpu_name_error"] = str(exc)

    model = structural.StructuralModel(
        candidate="diffusion", seed=73115, simulations=512,
        drift_per_second=0.000001, drift_cap_per_second=0.00005,
        residuals=(-1.0, 0.0, 1.0),
    )
    states = tuple(
        structural.StructuralState(
            current_value=100_000.0 + index * 8.0,
            strike=100_075.0, seconds_remaining=480,
            volatility_per_second=0.0005,
            recent_return_5s=0.00001,
        ) for index in range(24)
    )
    try:
        gpu = choose_structural_gpu_backend(model, states)
        if gpu is not None:
            report["structural_backend"] = "cuda"
            report["structural_parity_verified"] = True
        else:
            report["structural_parity_verified"] = False
        report["xgboost_backend"] = xgboost_device()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
