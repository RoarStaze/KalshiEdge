# Parallel deterministic holdout scoring and model-training audit

**2026-11-14 · KalshiEdge**

## Goal
Reduce the one-logical-core bottleneck in full-model **SCORING**, including candidate/incumbent 480s checkpoint comparisons, and parallelize the remaining calibration structural scoring without modifying probabilistic predictions, thresholds, contracts, labels, independent holdout boundaries, or model promotion.

## Verified repository observations
- `structural.predict_probabilities` is already a deterministic `ProcessPoolExecutor`-aware mapper: each row retains exactly its `StructuralModel.predict_proba` seed and ordered output. The full-model training stage `training._structural_training` already uses 3 Windows CPU processes by default (1-4 configurable with `KALSHI_TRAIN_STRUCTURAL_WORKERS`), optional CPU-verified CuPy GPU path. **Do not reinvent the Monte Carlo algorithm**.
- `training.FittedBootstrapPipeline.predict` and `base_candidate` still use serial Python list comprehension to compute structural probabilities. `evaluate.evaluate_lockbox` calls this full pipeline on potentially thousands of holdout checkpoints; `run_lockbox_evaluation` can repeat full-pipeline inference for incumbent and 480-second pairs.
- Calibration in `training.build_experiment_bundle` computes structural scores serially after existing training's pool closes.
- `SCORING` is fixed UI progress 67%, not actual completed checkpoint rows.
- User reported PID 9900 calculating 19.25 CPU seconds in a 20-second interval during `SCORING`, with a 1.57GB memory footprint. This is **evidence for a serial CPU bottleneck, not definitive profiling**.

## Implementation decision
1. Add keyword-only *optional* `structural_executor` and inner `structural_progress` to both `FittedBootstrapPipeline.predict` and `base_candidate`; default `None` gives the exact serial current production path and existing pickled model compatibility. When provided, call the existing `structural.predict_probabilities` preserving exact row order and probabilities.
2. Use one shared Windows-safe spawn `ProcessPoolExecutor` for the entire full-model holdout evaluation, including candidate and incumbent comparisons; make worker count reuse `training._structural_worker_count` (3 default on 12 logical cores, ceiling 4, respecting existing override). In non-Windows CI default to serial. Ensure pool closes on errors.
3. For comparisons requiring only `candidate`, call `base_candidate` to avoid computing zero-weight structural and unnecessary logistic/structural diagnostics. Confirm bit-for-bit parity against full `predict()['candidate']`; preserve the initial full lockbox diagnostic metrics exactly and all original promotion gates.
4. Add *real* scoring counters based on completed structural-row chunks to the existing `training_runs/current.json` worker state, without misrepresenting the fixed 67% as fractional completion. Preserve counters through later stages but never overwrite previous model reports; frequency throttled to avoid excessive disk I/O.
5. Use the same already-tested parallelism for the final calibration structural probability computation, after completing structural OOF training; keep CPU RAM/thermal headroom and abstain from new GPU/XGB tuning or numerical optimization without equivalent output evidence.

## Non-goals and safety
- No connection to the user's Windows PC or running process; **GitHub-only branch**. Existing run continues under its original code until the user chooses a later local update.
- No changes to trading/AutoTrade, confidence gates, settlement algorithms, model weights, serialization schema or new GPU dependence.
- No claims about actual RTX 4050 wall-clock gains without running benchmarks on user's machine.
- Do not deploy or change TimesFM3 research logic.
