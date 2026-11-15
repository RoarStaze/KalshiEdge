# RTX 4050 GPU training and evaluation: verified routing

**November 14, 2026.** This document is a code audit of the KalshiEdge training and evaluation pipeline, NOT proof that a specific laptop's RTX 4050 currently has a functioning CUDA runtime.

## Supported GPU workloads

| Task | GPU path | Guardrail |
| --- | --- | --- |
| Stage 4, structural candidate selection and chronological folds | CuPy float64 CUDA kernel | Exact CPU random stream; GPU-vs-CPU parity, near-strike CPU replay, measured speed before `auto` selection |
| Stage 4, XGBoost classifier training/tuning | XGBoost native CUDA | Actual two-tree CUDA smoke fit verifies booster device; no longer blocked by unavailable CuPy |
| Stage 4, calibration structural simulation | CuPy float64 CUDA kernel | Same per-model parity and measured speed selector; otherwise capped multicore CPU |
| Stage 5, independent holdout initial structural metrics | CuPy float64 CUDA kernel | Only after the original verified bundle, lockbox scope, feature-schema, and leakage checks; identical simulation counts/seed |
| Stage 6, incumbent and exact 480-second comparisons | Reused verified CuPy GPU backend | Cached per verified model bundle, only when structural component is needed; otherwise existing capped multicore CPU |
| TimesFM-3 separate research experiment | Independent optional PyTorch CUDA | Restricted to separate noncommercial, nonproduction research and licensing; not integrated with live models |

## Tasks deliberately kept on CPU

- Signature verification, source manifest hashing, historical file/Parquet reads and dataset integrity: dominated by storage and host CPU checks, not CUDA kernels.
- scikit-learn logistic regression, histogram gradient boosting, residual model fitting, calibration mapping and constrained ensemble optimization: changing to unrelated GPU algorithms would affect numerical results and can change the selected model.
- **Live production final forecasts/AutoTrade**: GPU imports and kernel startup are unnecessary and could compromise fault tolerance or real-time latency; no CUDA dependency is added.
- XGBoost model inference after training: fitted CUDA models are set to `device="cpu"` before serialization so live forecasts remain portable.

## Runtime policy

- `KALSHI_TRAIN_GPU=auto` (default): try CUDA only after model-specific numerical parity and a representative CPU-vs-GPU speed check. Failed CuPy import, missing CUDA runtime headers/NVRTC, low VRAM, slow GPU or non-identical results -> same seeded CPU path. This **does not guarantee GPU use**; RTX 4050 float64 throughput can be limited.
- `KALSHI_TRAIN_GPU=on`: require CUDA availability for GPU-eligible operations and fail clearly if parity/setup fails, rather than silently claiming acceleration. This may be **slower** than CPU.
- `KALSHI_TRAIN_GPU=off`: do not import/initialize optional GPU backend.

CPU parallel fallback remains the bounded policy from PR #149: default three Windows worker processes with >=8 logical CPUs, maximum four with `KALSHI_TRAIN_STRUCTURAL_WORKERS`. This does not imply full saturation of the i5-13420H on every pipeline stage.

All structural GPU acceleration uses **512 seeded Monte Carlo simulations** for a full-model candidate; the structural model's generic 4096-simulation class default is NOT the training configuration.

## Read-only device audit on Windows, after current training finishes

This repository work **does not touch your local files, Git version, GPU installation or running training worker**. Run these commands **only after the existing training run is finished** and after choosing to update local code:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.bootstrap.cuda_training --check
```

The resulting JSON reports:
- `mode`: auto/on/off
- `cuda_available`: CuPy-runtime CUDA available (structural simulation eligibility)
- `structural_backend`: verified fast CUDA or CPU fallback on the 24-market-point benchmark
- `xgboost_cuda_build`: independent XGBoost CUDA wheel capability
- `xgboost_backend`: CUDA if its **real smoke fit** uses GPU, even when CuPy is unavailable
- `gpu_name` or `gpu_name_error` when CuPy can query the device

**Do not interpret CuPy `CUDA_PATH` warnings as proof of an unusable GPU.** CuPy may detect the NVIDIA device but still lack the matching NVRTC headers/toolkit to compile kernels. Official CuPy setup and diagnostic instructions: https://docs.cupy.dev/en/stable/install.html . Official XGBoost CUDA instructions: https://xgboost.readthedocs.io/en/stable/gpu/ .

KalshiEdge's optional `research-gpu` extra installs CuPy CUDA 12 and CUDA-capable XGBoost into the chosen Python environment; ordinary `research` installs `xgboost-cpu`. Do not install conflicting `xgboost-cpu` and `xgboost` wheels simultaneously or change those dependencies during an in-progress training worker. CUDA availability and actual numeric speed must be measured **on the user's own hardware** before claiming successful GPU acceleration.

## Compatibility and integrity

No model weights, Monte Carlo random stream, saved model serialization layout, schema, confidence gates, official settlements, or AutoTrade behavior were modified. GPU backend exists only as a runtime argument; nothing GPU-specific enters a promoted model's pickle. The one-time independent lockbox is still validated and scored under unchanged promotion rules.

CPU-only GitHub CI can validate option routing, automatic fallbacks, real CPU numerical parity, and failure policy; **it cannot establish RTX 4050 VRAM, CUDA kernel fidelity or wall-clock gain.**
