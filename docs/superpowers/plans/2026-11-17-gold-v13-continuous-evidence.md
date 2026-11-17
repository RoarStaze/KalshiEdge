# GoldPredict V1.3 Continuous Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans; test first and confirm GitHub CI before merging.

**Goal:** Collect KXGOLD15M prospective public quote, orderbook and trades snapshots continuously with durable source evidence while preserving a separate unverified Pyth settlement gate.

**Architecture:** Reuse existing quote capture, add a microstructure sample with bounded GETs and an immutable per-cycle ledger, expose a dedicated CLI mode and offline status. Use a single-process OS file lock and back off errors.

**Tech Stack:** Existing Python 3.12+, httpx, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-17-gold-v13-continuous-evidence-design.md`.

## Global constraints

- Gold KXGOLD15M only; no Bitcoin, authentication, order execution, or model promotion changes.
- Every persisted record remains explicitly `ready_for_training=false`, `settlement_verified=false`.
- Quote/market/orderbook/trades are sequential, not authenticated as-of exchange publication.
- Preserve immutable SHA-256 named evidence and explicit incomplete pagination.
- Do not authenticate or claim verified Pyth origin without external authoritative receipts.

## Review focus

- Duplicate or invalid market/trade identities must be rejected, not added to trainable data.
- A failed or HTTP 429 response must write a failure event and back off.
- Restarts must preserve earlier cycle files and prohibit overlapping collectors.
- Integrity checks must reject tampered artifacts and unsupported ready/verified values.
- Ctrl+C must exit without deleting captured data or opening orders.

### Task 1 — Microstructure evidence

**Files:** `src/kalshi_edge/gold/continuous.py`, `tests/gold/test_continuous_v13.py`

- [x] Write regression tests for immutable microstructure, capped book/trade evidence, negative quantities and source identity.
- [x] Implement `capture_gold_microstructure` and `inspect_gold_microstructure_snapshot`.
- [ ] Verify test suite with green CI.

### Task 2 — Continuous collector and recovery

**Files:** `src/kalshi_edge/gold/continuous.py`, `tests/gold/test_continuous_v13.py`

- [x] Add an OS-held lock, immutable per-cycle events, status inspection, retries and Ctrl+C termination.
- [x] Write bounded two-cycle and 429 recovery regressions.
- [ ] Verify tests, confirm valid error/status reporting under failures.

### Task 3 — CLI and runbook

**Files:** `src/kalshi_edge/gold/audit.py`, `docs/GOLD_CONTINUOUS_V13_2026-11-17.md`, `tests/gold/test_continuous_v13.py`

- [x] Add `--collect-continuously`, `--collector-status`, `--interval-seconds`, `--max-cycles`, `--max-markets`.
- [x] Document PowerShell commands and settlement-verification limitations.
- [ ] Confirm all existing gold commands and tests unchanged.

### Task 4 — Full verification and merge

- [ ] Verify GitHub CI compile, smoke, full pytest and production build.
- [ ] Confirm code diff gold-only; confirm no model/credential/trading paths changed.
- [ ] Merge to `main` only if checks pass; provide exact `git pull` and PowerShell runner commands.
