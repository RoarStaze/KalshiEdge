# WeatherEdge V7 Adaptive Nowcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real hourly Kalshi Weather Index predictor learn robust, regularized trend coefficients from source-audited historical minute readings and deploy only on held-out improvement.

**Architecture:** A pure regression helper fits forecast error from timestamp-correct hourly cases; existing V2 nowcaster considers it alongside static candidates. The same pair of fit/holdout gates and the hourly UI's evidence status govern live use; no market system changes.

**Tech Stack:** Python 3.11+, SQLite, standard-library math, pytest.

**Spec:** docs/weather/hourly_adaptive_nowcast_v7.md

## Global Constraints
- No external APIs, paid services or user setup; uses the already-running Kalshi Index collector.
- Weather live orders remain disabled.
- Frozen decision-time data, no target-hour label leakage; fit regression and sigma only on training hours.
- Best model selected by genuine paired chronological MAE/CRPS/coverage, never invented confidence.

## Review Focus
- One giant training outlier cannot dominate coefficients.
- Fast/slow highly collinear inputs remain finite and bounded.
- Changed holdout outcomes do not retune weights or error scale.
- No model deploys on insufficient or worsening holdout results.
- Live forecast reports actual selected model and scores and does not create orders.

---

### Task 1: Robust regression candidate
**Files:** Create src/kalshi_edge/weather/hourly_adaptive.py, tests/weather/test_hourly_adaptive.py
- [ ] Write failing tests for bounded fit, outlier stability, source-time-consistent live prediction.
- [ ] Verify red in CI; implement robust regularized 3-parameter solver.
- [ ] Verify green and no test regressions.

### Task 2: Automatic nowcast competition and live integration
**Files:** Modify src/kalshi_edge/weather/hourly_nowcast.py, tests/weather/test_hourly_adaptive.py.
- [ ] Test that synthetic hourly pattern selects adaptive over static models, unmodified holdout chooses same coefficients; poor holdout rejects.
- [ ] Add to train-only candidate comparison, preserve existing static candidates and live safety gates.
- [ ] Verify full test suite.

### Task 3: Explain actual predictor and verify
**Files:** Modify src/kalshi_edge/weather/hourly_ui.py, tests/weather/test_hourly_adaptive.py, docs/weather/hourly_adaptive_nowcast_v7.md.
- [ ] Test that selected live model and real measured improvement appear in forecast technical details with uncertainty certification disclaimer.
- [ ] Run full CI + container; inspect diff; merge PR to main on success; give git pull and kalshiedge start.