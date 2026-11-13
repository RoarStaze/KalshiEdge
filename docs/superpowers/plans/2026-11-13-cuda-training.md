# Optional CUDA Training for RTX 4050 — implementation plan

## Goal
Actually execute Stage-4 structural Monte Carlo on NVIDIA CUDA and use GPU-enabled XGBoost when available, while preserving the independent lockbox and training-result safety contract.

## Design decision
1. Add an isolated optional CuPy CUDA backend for *training-only structural model probability evaluation*. Keep production `StructuralModel.predict_proba` on its existing CPU algorithm.
2. Preserve the *exact seeded CPU random shock stream* (`random.Random(seed).gauss` or `randrange`) for each `(model, seconds_remaining)`, reuse the same shocks across rows, and offload expensive path propagation to the GPU with a CuPy CUDA double-precision kernel. GPU produces the same simulation classifications as the CPU in representative parity tests; near-boundary outcomes and numerical invalidities are retried with the original CPU implementation.
3. Benchmark CPU vs CUDA on realistic validation rows at worker startup; use CUDA only if installed, verified, and faster. `KALSHI_TRAIN_GPU=auto|on|off`; `on` requires compatible CuPy/CUDA and fails with an actionable message rather than silently pretending to use CUDA. `off` preserves CPU.
4. When CUDA is selected, skip the Windows CPU process pool for structural probabilities; otherwise retain existing multi-core CPU path. Emit `CUDA` / `CPU` and verified progress details to Train page through existing progress recorder.
5. Add CUDA device routing to XGBoost *only* when the GPU-capable XGBoost build is installed and CUDA has passed its probe, with bounded GPU memory and no silent fallback to a different model within one candidate evaluation. Keep current CPU behavior for CPU-only installations.
6. Add an optional `research-gpu` dependency extra, avoiding co-installation of `xgboost-cpu` and full `xgboost`. Do not change locked default research dependencies or Docker environments.
7. Tests must validate CPU fallback, exact seeded shock sequence, deterministic GPU backend API via stubs and real GPU conditional tests, speed-gate semantics, XGBoost device routing, and no accidental changes to independent evaluation / checkpoints. CI may be CPU-only: report hardware benchmark as unverified until user runs it on RTX 4050.

## Safety
- No changes to feature schema, lockbox partitions, evaluation gates, trade execution, or automatic model promotion.
- No training run is touched until the user closes KalshiEdge and upgrades.
- Model/calibration parity not assumed: verify GPU vs CPU on deterministic reference rows before enabling. Retain reproducible row-level seeds.
- If CUDA fails during training, explicitly log failure and revert to CPU for an *uncommitted training-only operation* before recording its completion; never deploy an unverified GPU artifact.

## Task slices
A. Red tests for optional GPU activation/probe/fallback and seed equivalence.
B. Implement and test CUDA structural kernel and GPU speed/correctness probe.
C. Wire into Stage-4 structural fitting, record actual backend.
D. Wire XGBoost CUDA and optional dependency set.
E. Green GitHub Actions, review, squash merge to main; give PowerShell update/install/benchmark instructions.