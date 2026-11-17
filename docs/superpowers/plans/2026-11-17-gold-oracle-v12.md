# GoldPredict V1.2 Settlement Oracle Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans; test first and verify RED/GREEN.

**Goal:** Produce independently auditable **unverified** Pyth OHLC source artifacts and reason-coded KXGOLD15M settlement audits without inventing labels.

**Architecture:** Offline strict Pyth Pro OHLC importer preserves original JSON bytes inside SHA-256-named artifacts; a separate settlement assessment cross-checks hashed Kalshi inventory and Pyth evidence without manufacturing settlement labels. Existing gold dataset readiness accepts the new evidence but stays fail-closed until independent source authenticity, candle times and contemporaneous feature publication are established.

**Tech Stack:** Python >=3.12, pytest, existing gold/prospective immutable-file tools; Pyth Pro REST documentation.

**Spec:** docs/GOLD_SETTLEMENT_ORACLE_V12_2026-11-17.md

## Global Constraints

- Gold-only; never alter Bitcoin predictor, keys, order placement, trading or model promotion.
- Pyth `Metal.Index.1OZGOLD/USD`; OHLC `resolution=1`, explicit channel and Unix seconds.
- Offline import only; no API keys or authenticated Pyth requests.
- Preserve the original JSON body and SHA-256; integrity is not authenticity.
- No verified settlements or training rows until independent evidence supports the rules, source, channel, and as-of availability.

## Review Focus

- Forged `training_ready=true` must fail even with recomputed artifact hash.
- Invalid, duplicated, missing or out-of-window minute bars must fail.
- Contradictory Kalshi outcomes and mixed EST/EDT must be reason-coded.
- Imported OHLC must not become executable price, historical prediction feature or training label.
- CLI modes must require explicit source files and exact request range.

### Task 1 — Pyth local history evidence importer

**Files:** `tests/gold/test_oracle_v12.py`, `src/kalshi_edge/gold/oracle_history.py`

- [x] Write regression tests before implementation.
- [x] Observe RED (missing oracle_history module).
- [ ] Implement strict validation, request provenance plan and SHA import.
- [ ] Run tests and verify GREEN.

### Task 2 — Settlement evidence audit

**Files:** `src/kalshi_edge/gold/settlement_evidence.py`

- [ ] Validate source hashes, rules and conflicting market results.
- [ ] Reason-code every ticker and enforce zero independently verified labels.
- [ ] Verify tests with synthetic inputs and full CI.

### Task 3 — Dataset/CLI/UI integration

**Files:** `src/kalshi_edge/gold/dataset_readiness.py`, `audit.py`, `research_ui.py`

- [ ] Add `--import-pyth-history` and `--verify-settlement-evidence`, allow importing hash into dataset audit.
- [ ] Preserve previous gold command behavior and add explicit blocked UI state.
- [ ] Run complete gold and repository test suites.

### Task 4 — Documentation, independent validation and merge

**Files:** `docs/GOLD_SETTLEMENT_ORACLE_V12_2026-11-17.md`

- [ ] Confirm primary Kalshi rulebook and Pyth API sources; document unresolved rules and credentials blocker.
- [ ] Run GitHub Actions compilation, full tests and container build.
- [ ] Merge successful PR to main and verify main commit and exact local PowerShell commands.
