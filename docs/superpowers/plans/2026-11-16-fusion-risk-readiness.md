# Fusion risk-distance validity and readiness accounting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) for tracking.

**Goal:** Recover valid, high finite BRTI-distance cases that Fusion v2 currently excludes, and show precise reasons that individual 480s cases fail evidence readiness.

**Architecture:** Continue using the same existing regularized calibration, same chronological barriers, same 480 eligible examples, and same holdout approval. Extend only a previously mismatched risk input domain (valid finite z>100); version the signed fusion artifact schema to v3, fail closed for existing v1/v2 artifacts until the operator backs them up explicitly, and return aggregate non-sensitive exclusion counters from the existing read-only verification path. No speculative probability formula and no trading or threshold change.

**Tech Stack:** Python 3, existing Pydantic models and snapshot integrity checks, GitHub Actions pytest.

**Spec:** This document. Existing `docs/LIVE_FITTED_SIGNAL_FUSION_480_2026-11-16.md`.

## Global constraints

- No historical backfill, fake labels, artificial features, model re-training or trading.
- Keep exact active model hash, strict >63% predicted-side gate and 0.20 risk veto.
- Keep chronological training/calibration/evaluation, all acceptance thresholds and 480 minimum.
- Accept only real finite non-negative BRTI risk distances; reject NaN, infinities, negatives and booleans.
- Keep risk transformed into bounded existing signal features; no extra model inputs or fitted weight guess.
- Signed v1/v2 artifacts cannot silently use expanded input semantics: explicit backup and refit needed.
- Status reports counts only (not market identities or secret data); exclusions sum to same-model finals.
- Preserve output keys `eligible`, `same_model_finals` and `missing_or_invalid` for compatibility.

## Review focus

- High, finite z=500 must produce finite signals and be evaluated like other valid distances.
- z=None/negative/NaN/inf/bool must never enter model or scoring.
- Existing v2 cryptographically valid active artifact must not be silently reused.
- Missing digest-checked snapshot and invalid official settlement have distinct exclusion counts.
- Status reports must not mutate local evidence; fit still requires 480 and independent holdout.

---

## Task 1 — Tests (RED)
- [ ] Write tests for high BRTI z inputs, existing model artifact rejection and reason-specific evidence exclusions.
- [ ] Verify the test suite fails for absent behavior in CI.

## Task 2 — Implementation (GREEN)
- [ ] Implement v3 input contract and grouped exclusion reasons, with v1/v2 fail-closed migration.
- [ ] Run focused and full CI; correct failures with fresh evidence.

## Task 3 — Integration
- [ ] Document migration, status output, unchanged safeguards and local commands.
- [ ] Merge through PR to main only once full CI is green and then provide PowerShell update commands.
