# GoldPredict point-in-time dataset and XGBoost foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task by task. Tests-first and verification gates apply.

**Goal:** Add prospective gold quote capture, immutable fail-closed dataset audits and a gold-only, nondeployable XGBoost research baseline.

**Architecture:** Gold source timestamps and API record hashes are preserved without treating historical candles as timely features. A dataset gate produces zero trainable rows until independent Pyth oracle and contract rule verification. An isolated research baseline operates only on explicitly supplied rows and cannot be promoted to the live predictor; real dataset audit artifacts cannot be trained.

**Tech Stack:** Python >=3.12, existing httpx, XGBoost research dependency, pytest, Kalshi REST GET.

**Spec:** Prior approved GoldPredict V1 design and verified docs/GOLD_* audit artifacts.

## Global Constraints
- KXGOLD15M only; exact reference Metal.Index.1OZGOLD/USD.
- Bitcoin predictor, orders, credentials, autotrade and training are unchanged.
- Immutable SHA-256-named files; no historical price pretending to be observed.
- Not deployable; no live gold forecasts, training or trading gates enabled.
- Do not produce numeric performance claims from real gold markets absent verified labels.

## Review Focus
- Forged source-ready fields should fail before producing artifacts.
- Tampered SHA snapshot should fail before producing artifacts.
- Malformed quotes, cross-series tickers, bad timestamps and finalized markets should fail.
- Retrospective candles and API results cannot silently become a training row.
- Feature publication or label availability after the decision time must be rejected.

## Tasks

### Task 1: Prospective gold quote receipts
**Files:** Create src/kalshi_edge/gold/prospective.py; Test tests/gold/test_dataset_milestone.py.
- [x] Write failing tests covering public-only GET, request/receipt times, hashed artifacts and malformed fields.
- [ ] Observe RED (missing module).
- [ ] Implement bounded public series/market snapshot with explicit incomplete pagination and quote validation.
- [ ] Observe GREEN in CI.

### Task 2: Immutable dataset readiness audit
**Files:** Create src/kalshi_edge/gold/dataset_readiness.py; Test tests/gold/test_dataset_milestone.py.
- [x] Write missing-module tests for source SHA, fail-closed gate, forged metadata.
- [ ] Observe RED.
- [ ] Validate source artifacts, preserve source hashes, reject unsupported oracle claims, write zero-training-row evidence audit.
- [ ] Observe GREEN in CI.

### Task 3: Gold-only research XGBoost baseline
**Files:** Create src/kalshi_edge/gold/xgboost_baseline.py; Test tests/gold/test_dataset_milestone.py.
- [x] Write tests for chronological session split, feature/label timing, nondeployable results and audit blocking.
- [ ] Observe RED.
- [ ] Implement research-only split/evaluation, no model promotion or trading API routes.
- [ ] Observe GREEN in CI.

### Task 4: CLI, documentation and verification
**Files:** Modify src/kalshi_edge/gold/audit.py; document docs/GOLD_DATASET_AND_XGBOOST_V1_2026-11-17.md.
- [ ] Implement read-only --capture-quotes and --audit-dataset CLI modes, no write APIs.
- [ ] Run full CI, inspect logs and correct failures.
- [ ] Merge branch to main only after passing tests.
