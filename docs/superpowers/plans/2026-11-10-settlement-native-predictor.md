# Settlement-native predictor challenger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a causal, non-trading settlement-window BRTI probability challenger with independent prospective evidence.
**Architecture:** Pure analytic function; immutable post-final research record and reader; live capture after existing final writer; separate official-settlement evaluation CLI.
**Tech Stack:** Python 3.11+, Pydantic v2, existing KalshiEdge bootstrap modules, pytest, GitHub Actions.
**Spec:** `docs/superpowers/specs/2026-11-10-settlement-native-predictor.md`

## Global Constraints
- Do not alter active final-forecast decisions, AutoTrade or model promotion.
- Never reconstruct causal BRTI input using future settlement data.
- Capture the challenger only after the existing final forecast is durably written.
- Respect a 480-second checkpoint, 60-second closing-average target, and 5-second maximum BRTI source age.
- Reject tampered or mismatched records, and preserve separate paired/issued denominators.
- No new dependencies or long-running model training.

## Review Focus
- Source timestamps after the forecast checkpoint must fail closed.
- Missing or zero realized volatility must never yield false certainty.
- Corrupted record hashes or changed original final forecasts must be rejected.
- A skipped incumbent forecast is not automatically an executed trade.
- A mismatch between settlement identity and final record must not produce a paired result.

### Task 1: Analytical baseline and causal validation
**Files:** Create `src/kalshi_edge/bootstrap/settlement_challenger_480.py`; create `tests/bootstrap/test_settlement_challenger_480.py`.
- [ ] Write probability tests for 60-second average variance and extreme/invalid inputs.
- [ ] Confirm test initially fails because implementation is missing.
- [ ] Implement deterministic, dependency-free analytic probability.
- [ ] Verify test behavior.

### Task 2: Immutable post-final records
**Files:** same module and test file.
- [ ] Test timestamp, identity, SHA-256 binding, missing-input status, idempotent create-once behavior.
- [ ] Implement causal record and fail-closed loader/writer.
- [ ] Verify tests, including tampering.

### Task 3: Continuous collection
**Files:** Modify `src/kalshi_edge/bootstrap/live.py`; test module.
- [ ] Test that collection occurs only after immutable final is saved.
- [ ] Add 480s research capture with isolated errors and no model-selection changes.
- [ ] Verify no trading or final forecast behavior changed.

### Task 4: Independent prospective scorecard
**Files:** same module and tests, documentation.
- [ ] Test one-market-one-pair and invalid/missing/pending outcome accounting.
- [ ] Score matched incumbent/challenger on the same official settlements; report probability and coverage metrics.
- [ ] Add read-only CLI entrypoint.
- [ ] Run full CI, review results, and merge only after green; include local PowerShell update command.
