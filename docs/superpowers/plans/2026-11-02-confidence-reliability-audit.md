# Reliability by confidence band — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Complete test-first red/green verification and full CI before merge.

**Goal:** Identify whether reducing KXBTC15M final SKIPs could add *correct* forecasts, while keeping 480-second prospective validation distinct from legacy 240-second hindsight.

**Architecture:** A new immutable, read-only CLI loads locally saved final decisions and matching official verified settlements. It reports fixed, predeclared confidence bins, hypothetical would-be outcomes for recorded confidence SKIPs, actual issued-forecast accuracy, and Wilson uncertainty bounds. It never searches thresholds, retrains, changes decisions, or trades.

**Tech Stack:** Python 3.12+, Pydantic existing models, stdlib stats/math, pytest.

**Spec:** This document.

## Global constraints
- Existing 69% confidence gate and frozen specialist untouched; no change to live forecast, automated trade, or protected settlement records.
- Fixed bins: [0,0.2), [0.2,0.4), [0.4,0.6), [0.6,0.69), [0.69,0.8), [0.8,0.9), [0.9,1].
- Separate (a) legacy 240s; (b) 480s at/before 2026-11-02 13:00 UTC; (c) 480s strictly after fixed research-policy registration.
- Every scored row requires valid same-ticker same-close official settlement; enforce filename/day consistency and duplicate exclusion.
- A hypothetically correct skipped *direction* is NOT a correct issued forecast or realized trade.
- Report Wilson 95% CIs and mark small samples; no model-promotion advice.

## Review focus
1. No mixing 240s with later 480s confidence distributions or labels.
2. Strict < vs >= 0.69 and 0.9 confidence band boundaries.
3. Exclude invalid or missing outcomes from bin sample; track them separately.
4. Refuse misleading probability rows (missing p_yes, inconsistent confidence) rather than inferring precision.
5. No file writes, API calls, exchange actions, learned weights or threshold fitting.

## Task 1: Characterize confidence reliability without hindsight-selected gates
- [ ] Add pytest regressions for 240/early-480/forward-480 partitions, missing/invalid settlement, fixed band boundaries, correct vs skipped counterfactual accuracy, and read-only behavior.
- [ ] Observe CI red from missing module.
- [ ] Implement `src/kalshi_edge/bootstrap/confidence_reliability_480.py` CLI and only the required pure metric helpers.
- [ ] Re-run all tests and production container workflow; inspect CI and PR diff.
- [ ] Update docs with a one-command PowerShell operator report and limitations.
- [ ] Merge only after current-head CI success.
