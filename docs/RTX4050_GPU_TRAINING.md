# KalshiEdge NVIDIA GPU training (RTX 4050, Windows)

## When to upgrade

Finish any running training session first. Updating Python/NumPy/XGBoost/CuPy
while a baseline fit is running can interrupt its in-memory optimizer.
Close KalshiEdge, then update the GitHub `main` checkout and its Python
dependencies before starting the *next* training run.

## Install the actual GPU backend

From PowerShell in `$HOME\Desktop\KalshiEdge`:

```powershell
git switch main
git pull --ff-only origin main

# Confirm NVIDIA's driver supports CUDA. XGBoost 3.4's GPU wheel requires
# CUDA >= 12.9 driver compatibility.
nvidia-smi

$py = ".\.venv\Scripts\python.exe"

# Replace the existing CPU-only XGBoost variant with its GPU-capable package.
& $py -m pip uninstall -y xgboost-cpu
& $py -m pip install -e ".[research-gpu]"

# This is a real GPU compile, smoke fit, historical parity and speed test.
& $py -m kalshi_edge.bootstrap.cuda_training --check
```

The last command prints a JSON report showing:
- `cuda_available: true` when a supported NVIDIA GPU and free VRAM are present.
- `structural_backend: "cuda"` only when CPU parity and an on-device speed test pass.
- `xgboost_backend: "cuda"` only when a *real* XGBoost CUDA smoke fit succeeds.
- The GPU name as reported by CUDA.

`KALSHI_TRAIN_GPU=auto` is the default: GPU runs when faster and verified.
To use GPU even if the structural microbenchmark is slower:

```powershell
$env:KALSHI_TRAIN_GPU = "on"
& $py -m kalshi_edge.bootstrap.cuda_training --check
# Launch KalshiEdge from the same PowerShell session.
```

To disable CUDA and use unchanged CPU training:

```powershell
$env:KALSHI_TRAIN_GPU = "off"
```

`on` fails with a clear error when CUDA cannot be initialized or verified.
`auto` retries structural work and XGBoost fits on CPU after GPU errors,
without treating incomplete GPU output as a completed trained model.

## What the GPU actually does

- **Stage 4 structural Monte Carlo**: a CuPy CUDA double-precision kernel
  propagates the exact original Python-seeded per-simulation random shocks.
  Near-strike floating-point boundary cases are recomputed on CPU. Results
  must match the original CPU predictions before CUDA is activated in auto mode.
- **Stage 4 XGBoost candidate fits**: the GPU-capable full XGBoost wheel
  performs CUDA tree fitting; the resulting estimator's inference device
  is reset to CPU for deployment portability.
- **Still CPU**: scikit-learn logistic/hist-gradient models, data integrity,
  feature assembly, regression/calibration and independent lockbox evaluation.

Training progress names the verified CUDA backend when selected.
This update does not alter training labels, simulation count, held-out data,
confidence thresholds, model promotion gates or trading rules.

## Limits and performance

GitHub's CPU-only CI cannot validate real RTX 4050 CUDA compatibility or speed.
The on-device `--check` command supplies that evidence.
GPU speedups depend on CUDA driver, available VRAM, workload size and GPU
floating-point throughput. An RTX 4050 is not inherently faster at all
double-precision calculations. GPU acceleration is used automatically only
when its verified benchmark beats CPU execution.

Do not co-install `xgboost-cpu` with full `xgboost` into the same venv.
The default `research` extra remains CPU-only; use `research-gpu` for
a CUDA training environment. Verify runtime after NVIDIA driver updates.

Official references:
- https://docs.cupy.dev/en/stable/install.html
- https://xgboost.readthedocs.io/en/stable/install.html
- https://xgboost.readthedocs.io/en/stable/gpu/
