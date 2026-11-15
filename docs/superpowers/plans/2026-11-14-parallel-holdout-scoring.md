# Parallel holdout scorer and calibration acceleration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Preserve exact numerical parity.

**Goal:** Reuse existing deterministic multiprocessing for holdout and calibration, and report actual holdout progress.

**Architecture:** Extend the existing pipeline's optional research-only predictor parameters; wire a shared Windows spawn executor through evaluation and calibration. Read-only test fixtures verify serial vs multicore identity, compare-only equivalence, and worker progress persistence. Production live inference remains serial by default.

**Tech Stack:** Python 3.13, standard library `concurrent.futures`, Pydantic, pytest, existing structural CPU path.

**Spec:** docs/superpowers/specs/2026-11-14-parallel-holdout-scoring.md

## Global Constraints
- Merge only after green full repository CI and production Docker build.
- No stochastic/algorithmic or model tuning changes, no new trading effects.
- Preserve source hash and prior worker run state; no modifications to user's local ongoing worker.
- Worker count defaults to existing capped Windows 3 (or override 1–4); other OS unchanged.
- Ensure Windows spawn picklability and no nested ProcessPoolExecutor deadlocks.

## Review Focus
- Exact probability and output order across serial and parallel execution.
- Structural-negative-weight incumbent never invokes unused 4096-path evaluator on candidate-only comparisons.
- Promotion/lockbox uniqueness gates and stale-evaluation checks still run before expensive inference.
- Exceptions release child workers; no stray subprocesses, no CUDA dependency.
- Progress counters represent actual completed scoring rows, not simulated progress percentages.

## Tasks
- [ ] RED tests for serial/parallel `FittedBootstrapPipeline` structural parity and `base_candidate` identity, plus true scoring-counter state.
- [ ] Introduce optional structural executor (default serial) in existing fitted predictor methods and calibration call.
- [ ] RED / GREEN for holdout executor reused through compare-only scoring and same-market 480s comparison, including mocked harness and native Windows spawn tests.
- [ ] Wire lightweight scoring callback to worker state; report completed structural-row counts.
- [ ] Full GitHub Actions CI, branch diff review, PR, CI, merge to main. Report that no user's local operation was touched.
