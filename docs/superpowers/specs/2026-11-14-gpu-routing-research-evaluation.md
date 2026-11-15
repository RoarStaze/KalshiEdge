# Safe GPU routing across KalshiEdge research training and evaluation

2026-11-14 · Requirements and design

## Objective
Use an NVIDIA RTX 4050 GPU wherever the *existing* KalshiEdge algorithms demonstrably support faster CUDA execution, with no numerical deviation in official settlement predictions, no lockbox contamination, and no change to live/AutoTrade execution. Use GitHub only, never touch the user's currently executing Windows training worker.

## Verified findings
- Structural Monte Carlo Stage-4 already supports an optional CuPy CUDA kernel with CPU seeded shocks and exact-CPU-parity smoke test. Existing default `KALSHI_TRAIN_GPU=auto` selects GPU only when bench faster on 24 historical rows; `on` forces GPU with fail-fast, `off` CPU only. Stage-4 uses `STRUCTURAL_SIMULATIONS=512`.
- XGBoost already has a `device="cuda"` runtime smoke fit, but current `xgboost_device()` first checks `_cuda_hardware_ready()`, which imports CuPy, unnecessarily disabling independently capable CUDA-enabled XGBoost on a system where CuPy/NVRTC is absent or misconfigured.
- PR #149 parallelized Windows CPU holdout and calibration. These stages still cannot receive `gpu_backend`, although `structural.predict_probabilities(...,gpu_backend=...)` already supports it.
- Other stages use DuckDB/PyArrow integrity verification, sklearn LogisticRegression, HistGradientBoostingClassifier, scipy calibration/stacking. No approved drop-in GPU equivalent can be substituted without changing model numerics/algorithms; remain CPU.

## Chosen approach
1. **XGBoost decoupling:** select GPU strictly from XGBoost's own CUDA-enabled wheel and real verified 2-tree smoke fit; do not require CuPy to be installed. Keep `KALSHI_TRAIN_GPU=on` fail-closed semantics.
2. **End-to-end structural GPU reuse:** add optional nonserialized runtime GPU argument to `training._structural_scores`, `FittedBootstrapPipeline.predict`, and `base_candidate`. On holdout, build *one selected and verified backend per unique fitted structural model* from representative already-authorized lockbox rows. Reuse during initial candidate scoring and all 480s candidate/incumbent comparisons; candidate-only zero-weight structural model should skip backend initialization entirely. In auto mode a failed parity, CUDA import, or speed gate falls back to the existing capped CPU worker pool without changing seed. In explicit-on mode surface failure; no silent fallback.
3. **Calibration:** run same GPU parity/speed selector on calibration rows; if GPU accepted use CUDA, otherwise existing capped CPU pool. Never change labels, calibration model or simulations.
4. **Transparency:** extend `cuda_training --check` to expose separate reason/status for CuPy CUDA and independently tested XGBoost. Add concise docs and PowerShell read-only check; no installation from GitHub tool or local operations.
5. **Bounded memory and precision:** retain original 512 path count, 32-row CUDA batches, 512 MB free VRAM threshold, fp64 kernel and near-strike CPU replay. No GPU for production live forecasts, which must not gain CUDA dependency.
6. **No training speed guarantee:** actual device availability, CUDA headers and runtime, GPU parity, and measured speed remain local-device tests. RTX 4050 FP64 may be slower than multicore CPU; `auto` must not force GPU.

## References
- XGBoost docs: https://xgboost.readthedocs.io/en/stable/gpu/
- CuPy installation/runtime dependencies: https://docs.cupy.dev/en/stable/install.html

## Boundaries
- No user PC access, process restarts, `git pull`, credentials, trade submissions, model promotion or final-forecast changes.
- No retraining the independent promotion lockbox during CI; synthetic regression only.
