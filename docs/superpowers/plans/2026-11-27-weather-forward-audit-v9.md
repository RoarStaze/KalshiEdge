# WeatherEdge V9 prospective audit implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, with test-driven-development on each task.

**Goal:** Collect and compare pre-settlement hourly WeatherEdge forecasts against exact official Kalshi Index and contract outcomes by city, lead and model.
**Architecture:** New module `hourly_forward.py` owns immutable SQLite snapshots, exact-source outcomes and auditing. Existing WeatherSupervisor invokes collection and reconciliation, and `hourly_calibration` exposes a read-only report rendered by the existing hourly UI.
**Tech Stack:** Python, SQLite, httpx, pytest, GitHub Actions Python 3.13, Docker.
**Spec:** `docs/weather/hourly_forward_audit_v9.md`

## Global constraints
- Keep all hourly weather predictions and BTC/Gold trade paths unchanged; weather is paper-only.
- Source timestamps, exact event/market ticker/threshold and original source hashes required.
- No inferred outcomes or replay of past forecasts as if first-observed.
- UI and API must never open SQLite writable; worker owns writes.

## Review focus
1. Freeze after close must be rejected; first freeze cannot be overwritten.
2. Future/late source receipt must be rejected.
3. Wrong ticker, strike, event, time, or ambiguous settlement cannot score.
4. Tampering or schema corruption must fail closed and yield no scores.
5. Scores must be split by city, nominal lead and model; n<10 remains unmeasured.

### Task 1: immutable prospective journal
Files: create `src/kalshi_edge/weather/hourly_forward.py`; create `tests/weather/test_hourly_forward.py`.
- [ ] Write failing checkpoint tests covering five horizons, frozen immutable snapshot, late receipt, wrong close, unsupported model/probability.
- [ ] Verify RED with GitHub CI.
- [ ] Implement `journal_forward(db_path, *, asof=None, forecasts=None)` and immutable schema.
- [ ] Verify GREEN focused tests.

### Task 2: exact independent settlement reconciliation
Files: modify `src/kalshi_edge/weather/hourly_forward.py`, tests.
- [ ] Write first failing test for matching official exact top-of-hour Kalshi index and exact settled ticker, plus wrong threshold and wrong target.
- [ ] Verify RED.
- [ ] Implement `settle_forward_once(db_path, *, client=None, clock=None)` with source bytes and hash integrity, no lookahead.
- [ ] Verify GREEN; missing and malformed sources remain pending.

### Task 3: read-only forward audit with metrics
Files: module + tests.
- [ ] Write failing tests for per-city/per-lead/per-model metrics and 10-hour suppression; tampered source hashes fail closed.
- [ ] Verify RED.
- [ ] Implement `forward_accuracy(root, *, db_path=None, preview=False)` with exact outcome replay.
- [ ] Verify GREEN.

### Task 4: automatic worker and UI
Files: modify `src/kalshi_edge/weather/runtime.py`, `src/kalshi_edge/weather/hourly.py`, `src/kalshi_edge/weather/hourly_ui.py`, tests.
- [ ] Write failing tests that one-command worker journals/settles and calibration includes V9 grouped accuracy; UI has accessible cohort labels and no orders.
- [ ] Verify RED.
- [ ] Wire and test `kalshiedge start` worker, read-only calibration API, JS cohort reporting.
- [ ] Run `pytest -q`, build Docker on exact final PR SHA, review for leakage/incorrect counting, merge to main only on success.
