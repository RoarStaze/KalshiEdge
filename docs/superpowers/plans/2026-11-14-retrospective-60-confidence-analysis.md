# Read-only 60% Holdout Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare original Model 1 and Model 2 on the same archived 480-second holdout at .60 and .69, without changing live model, promotion policy, or saved report.

**Architecture:** Isolated CLI helper validates original saved run/report/model/data identities, replays archived model probabilities in read-only descriptive mode, cross-checks original 69% gate exactly, and returns 60%-threshold snapshot. Distinct codepath from current one-time evaluation and auto-promotion.

**Tech Stack:** Python 3.13, Pydantic models, PyArrow, existing seeded structural scorer and optional CUDA, pytest, GitHub CI.

**Spec:** docs/superpowers/specs/2026-11-14-retrospective-60-confidence-analysis.md

## Global Constraints
- No new promotion logic, no AutoTrade, no activated model pointer writes, no new lockbox consumed, no change to automatic gate.
- Historical results are explicitly posthoc and cannot be presented as fresh holdout validation.
- Use exact existing 480s causal row-selection and confidence-gate logic.
- Fail closed on changed dataset SHA, missing candidate, unexpected model number, or mismatch with stored 69%-gate counts.

## Review Focus
- User may have Model 1 active, Model 2 manually active, or neither; verify identity instead of assuming.
- Current report could have been replaced by a newer training attempt; require explicit original digest or refuse mismatch.
- Dataset might have been rebuilt after the historical report; refuse different SHA.
- CPU/GPU comparison must use identical per-market probability logic; verify original 69% counts before reporting 60%.
- No accidental writes into `models/default.json`, `reports/evaluation-*`, or training state.

## Tasks
- [ ] RED tests for exactly bound 69% parity and 60% computation, input validation, model selection, and metadata provenance.
- [ ] Add isolated read-only CLI with exact hash and one-market-per-checkpoint selection.
- [ ] Documentation with Windows PowerShell commands; warn expensive replay and posthoc bias.
- [ ] Green bootstrap/full CI, Docker build, PR and merge only if checks pass.
