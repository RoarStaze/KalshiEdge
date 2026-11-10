# Unified Train Now Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Train Now the sole training action, with persistent visible stage progress, validated pass/fail decisions and accurate activation reporting.

**Architecture:** Reuse `TrainingController` as the single subprocess owner. Add a forward-480 worker to the existing worker command using the already verified `evaluate_forward_candidate`. Make progress an atomic, durable run state; display timeline and verified evidence in the Train page. Retain the old hash-sealed bundle promotion mechanism for eligible legacy training only.

**Tech Stack:** Python 3.11+, Pydantic, sklearn, vanilla JavaScript/CSS, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-10-unified-train-workflow.md`

## Global Constraints
- No side effects from `GET /api/training` beyond reading existing records.
- No direct order/network credential use by training workers.
- No forward specialist live promotion without compatible authenticated model artifact and independent prospective validation.
- Train status must reflect completed *stage* milestones, not speculative time progress.
- Same-origin POST with empty JSON body only.
- Keep existing legacy training / promotion tests green.

## Review Focus
- Stale cached readiness while a worker is running: ensure progress comes from persisted run state.
- Process death or dashboard reboot: must show INTERRUPTED, never success.
- Concurrent Train clicks: refuse second worker and keep prior log.
- Model that scores 100% on 15 forecasts but issues far fewer than incumbent: explicit VALIDATION FAILED and concrete reasons.
- Attempted model pass without deployment capability: show NOT DEPLOYABLE, not “model activated”.

## Tasks
- [ ] **T1: Worker contract.** Write failing tests for selector, start, truthful milestones, terminal errors. Implement durable forward mode in `training_cycle.py`.
- [ ] **T2: Evidence and decision.** Write failing tests for progress callbacks and detailed gate comparison. Implement in `forward_learning_480.py`.
- [ ] **T3: UI.** Write failing dashboard tests for exactly one Train Now button, timeline and refreshed PASS/FAIL detail. Change `dashboard_ui.py` without breaking routes.
- [ ] **T4: API consolidation.** Add tests rejecting legacy evaluate route; keep `/api/training/start` protected and functional. Change `final_history.py`.
- [ ] **T5: End-to-end.** GitHub Actions full test, compile, build, PR review, then merge if entirely green.
