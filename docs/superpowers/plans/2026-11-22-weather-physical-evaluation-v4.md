# WeatherEdge V4 Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkboxes.

**Goal:** Frozen paired HRRR/NBM + baseline evaluation and prospective promotion only on verified gain.
**Architecture:** Immutable snapshots, exact official index labels, city/config/lead training, locked candidate, strictly future prospective gate, read-only guarded use.
**Tech Stack:** Python, SQLite, existing WeatherEdge modules, pytest.
**Spec:** docs/weather/hourly_physical_validation_v4.md

## Global Constraints
- Weather paper-only; no extra secrets.
- All data must be point-in-time and original source hashes checked.
- No model promotion without verified future market evidence.

## Review Focus
- NOAA fields received after cutoff => reject.
- Missing/delayed official index target => no label.
- Corrupted hashes => fail closed.
- Candidate refit on lockbox => prohibited.
- Changed index config or unmatched settlement => baseline.

### Task 1: Point-in-time paired snapshots
**Files:** src/kalshi_edge/weather/hourly_physical_learning.py; tests/weather/test_hourly_physical_learning.py.
- [ ] Write failing tests for frozen asof/no future/missing exact-hour index/duplicate rows.
- [ ] Confirm CI red, implement immutable SQLite capture and joins, confirm green.

### Task 2: Training and candidate locks
**Files:** src/kalshi_edge/weather/hourly_physical_learning.py, tests/weather/test_hourly_physical_learning.py.
- [ ] Write tests for train-only candidate weights/sigma and holdout rejection.
- [ ] Fit convex mix on 64 older hours, validate on 24, freeze hash-signed lock.
- [ ] Run tests.

### Task 3: Prospective gate and live fallback
**Files:** src/kalshi_edge/weather/hourly.py, hourly_physical_learning.py, tests/weather/test_hourly_physical_learning.py.
- [ ] Test 32 untouched later hours plus 24 verified thresholds; wrong config/freshness rejects.
- [ ] Implement fixed-model lockbox scores, conditional use, unchanged order state.
- [ ] Run tests.

### Task 4: Automatic status and verification
**Files:** runtime.py, hourly_ui.py, docs/weather/hourly_physical_validation_v4.md, tests/weather/.
- [ ] Test one-command sampling and UI status counters.
- [ ] Wire into hourly supervisor; run complete test suite and production build.
- [ ] Review diff; merge to main only on green CI.