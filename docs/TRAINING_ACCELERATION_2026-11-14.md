# November 14, 2026: deterministic training + scoring acceleration

## User-observed symptom and confirmed root
An existing full-model training worker on an i5-13420H / RTX 4050 laptop held at `SCORING` (fixed stage 67%) for over one hour. Correctly sampled CPU counters showed 19.25 seconds of CPU usage in 20 wall seconds (~96% of one core), with process alive and no recorded pause request. Source review confirmed `FittedBootstrapPipeline.predict()` computed structural Monte Carlo with a sequential Python list comprehension, even though training already had `structural.predict_probabilities(..., executor=...)` with deterministic Windows spawn multiprocessing.

The training pipeline uses **`STRUCTURAL_SIMULATIONS = 512`**, NOT the generic StructuralModel class's 4096-simulation default. Historical 480s and other checkpoints still require many path evaluations.

## Optimizations
1. **Holdout candidate full diagnostics:** use `structural.predict_probabilities` with a single capped Windows spawn executor shared through holdout and comparisons. Each row's original independent `StructuralModel.predict_proba` is unchanged; ordered results and seeded paths remain identical.
2. **Incumbent / same-market 480s comparisons:** use existing `FittedBootstrapPipeline.base_candidate()` (which already skips zero-weight components) rather than the full diagnostic `predict()`. Both paths still use the same calibrator and optional volatility overlay, and exact probability parity is tested. Initial full-candidate lockbox diagnostics are preserved.
3. **Final calibration phase:** the independent calibration structural forecast loop now uses the same deterministic capped CPU executor instead of running one Python loop on a single logical CPU. It preserves exact numerical model outputs and future model format.
4. **Real scored-row counters:** `training_runs/current.json` stores `score_rows_completed`, `score_rows_total`, `score_detail` while actual structural rows are computed; the dashboard shows these confirmed counts within a phase instead of a permanent blank bar at stage 67%. Intermediate filesystem writes are throttled, final rows always published. These local per-phase counts are **not** the global seven-stage training percentage.

## What was audited but deliberately not changed
- Historical market feature import is already manifest-verified and uses resumable checkpoints. Replacing it with unverified parallel reads risks partial snapshots and invalid source attribution.
- Candidate selection already caches its feature matrix, uses chronology-preserving tuning and performs calibrated ablation selection. Altering the `n_jobs=1` parameter of XGBoost or the number of candidate folds may change floating point outcomes, model selection, bundle hashes, and promotion evidence; no changes without independent parity/accuracy tests.
- Structural fitting already selects CUDA only after real CPU parity *and* a speed benchmark, and defaults to 3 Windows processes with >=8 logical CPUs. The new code reuses the same bounded CPU worker policy (maximum 4); it does **not** force RTX 4050 use during scoring.
- No changes to training's 512 Monte Carlo paths, Kalshi settlement rules, feature schema, AutoTrade, confidence thresholds, or independent holdout gates.

## Platform behavior and performance claims
- Windows: default 3 CPU worker processes on a 12-logical-core CPU; maximum 4 using existing `KALSHI_TRAIN_STRUCTURAL_WORKERS` override. These are capped for 16GB-memory laptop headroom.
- Linux/CI: default 1 process, matching prior architecture. Tests force two spawned workers for parity.
- The exact speedup on the user's laptop is **unmeasured** until another run on that machine, and may depend on disk throughput, executor startup time, worker memory, CPU thermals and the fraction of scoring devoted to structural Monte Carlo.
- Each default row still uses CPU RNG and path arithmetic. A separate GPU-only scoring change would require exact numerical parity and observed speed before production.
- All new changes are GitHub-only. No remote GitHub merge updates code in an already-running local Python worker. **Do not pull, close, or restart an active training worker to adopt this optimization.** Once the current evaluation finishes or safely pauses, inspect results before applying the next local update.

## Verification gates
- Force synthetic Windows-style spawn workers and verify `FittedBootstrapPipeline.predict` and `base_candidate` match serial predictions element-for-element on the same rows, including structural probabilities.
- Verify zero-weight structural models are not invoked by candidate-only comparisons.
- Verify progress never reports incomplete or fabricated counts and shows only current-stage values.
- Run full bootstrap, full project tests and production Docker build in GitHub Actions.
- Historical evaluated-market counts and actual speedup cannot be measured without the user's personal archive and machine.
