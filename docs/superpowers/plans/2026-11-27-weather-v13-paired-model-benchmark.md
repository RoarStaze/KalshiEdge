# V13 Paired Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Superpowers test-driven-development, systematic-debugging and verification-before-completion.

**Goal:** Establish a reproducible on-device out-of-sample contest between the existing V12 predictor and a robust hybrid, and promote only if paired proper scores improve.

**Architecture:** A standalone hybrid candidate, a read-only walk-forward evaluator, then an opt-in-via-evidence guarded live chooser. Scores are per city and lead.

**Tech Stack:** Python 3.11+, SQLite, no extra model dependencies, pytest/GitHub Actions.

**Spec:** docs/weather/hourly_paired_model_benchmark_v13.md

## Task 1: Test-first challenger
- Write deterministic paired-model regression tests first.
- Verify expected missing-module failure in CI.
- Implement a Huber ridge hybrid with bounded sigma and fail-closed inference.

## Task 2: Walk-forward replay
- Build 3 disjoint later 24-hour test folds with expanding historical training prefixes.
- Evaluate V12 chooser and V13 on identical target hours (MAE, CRPS, coverage).
- Audit exact frozen market threshold and finalized outcome for Brier/log loss/directional accuracy, or keep metrics null.
- Verify no-lookahead and insufficient-evidence behavior.

## Task 3: Local real comparison
- Load original local Index source/receipt-timestamp evidence from SQLite.
- Export read-only JSON per city/lead, with real metrics when data exist and explicit sample limitations otherwise.
- Add CLI entrypoint and optional automatic generation on app startup.
- Wire candidate only behind all precommitted 3-fold and market-outcome gates.

## Task 4: Verification
- Run focused tests, entire pytest suite, end-to-end and production Docker on exact feature head.
- Inspect metrics and ensure no automatic claims of guaranteed accuracy/profit.
- Merge only after verified CI and provide local pull/launch/compare commands.
