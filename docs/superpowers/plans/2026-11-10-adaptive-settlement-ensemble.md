# Adaptive settlement ensemble 480s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Causally learn a conservative ensemble from the settled incumbent + PR #122 480s challenger and capture forward-only read-only evidence.
**Architecture:** Dedicated researcher, immutable hash-bound post-final ensemble shadow, isolated live hook, independent post-settlement audit CLI.
**Tech Stack:** Python 3.12+, Pydantic v2, pytest, GitHub Actions.
**Spec:** `docs/superpowers/specs/2026-11-10-adaptive-settlement-ensemble.md`

## Global Constraints
- Incumbent final and AutoTrade are unchanged, with no automatic promotion.
- Training labels are available only if their official fetched timestamps are earlier than the current forecast timestamp.
- Training must not use pending, invalid, mismatch, or current market outcomes.
- Capture research only after the final and the PR #122 challenger, and isolate exceptions.
- SHA-256 and exact same-market linkage; preserve skip, untrained, and unavailable denominators.

## Review Focus
- Settlements fetched after a checkpoint must be excluded even if they have already settled now.
- Tampered prior shadow or final file must not enter the learning sample.
- Duplicate current market cannot enter training.
- Insufficient pre-checkpoint history must not fabricate a valid forecast.
- Candidate improvement must be reported on a genuinely paired, post-capture cohort, not training examples.

### Task 1: Chronological fitter and tests
**Files:** Create `src/kalshi_edge/bootstrap/adaptive_ensemble_480.py`; `tests/bootstrap/test_adaptive_ensemble_480.py`
- [ ] Write failing tests for strict past-only training and deterministic blend/shrink selection.
- [ ] Run pytest and confirm missing module failure.
- [ ] Implement pre-checkpoint official-settlement selection and Brier-fitted weight/shrink.
- [ ] Run focused tests.

### Task 2: Immutable independent research record
**Files:** Same new module/test.
- [ ] Write failing tests for insufficient training, SHA mismatch, idempotent records, corrupted pointers and hash verification.
- [ ] Implement a read-only capture/writer/loader with validated prediction reconstruction.
- [ ] Run focused tests.

### Task 3: Isolated live capture and paired scorecard
**Files:** Modify `src/kalshi_edge/bootstrap/live.py`, new module and test, documentation.
- [ ] Write failing source-order and prospective paired-audit tests.
- [ ] Implement post-challenger live hook, CLI, coverage/accuracy and probability scorecard.
- [ ] Run focused tests, then full repository CI and review; only merge to main when checks pass.
