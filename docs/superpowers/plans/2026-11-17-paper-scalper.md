# Durable Paper Scalper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development task-by-task. Follow TDD and verification-before-completion.

**Goal:** Implement a complete paper-only KalshiEdge intramarket scalp lifecycle without modifying live trading execution.

**Architecture:** Pure existing `scalp_policy` remains the decision maker; new `paper_scalper` owns a persistent SQLite journal, public quote polling, simulation and risk gating. Inject it into the existing dashboard worker with independent GET/POST same-origin endpoints and an explicit PAPER SCALPER UI panel.

**Tech Stack:** Python 3.11, Decimal, sqlite3, pytest, localhost HTTP handler + vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-17-paper-scalper-design.md`

## Global Constraints
- Never access signed order clients or place real orders.
- Never change current 69% live final forecast gate, active model, or live AutoTrade entries.
- No inferred fills without real public depth; all P/L is modeled, not actual.
- Restart disables new entries but tracks an existing paper position until verified exit/settlement.
- No automatic real-money activation, no martingale or side switching.

## Review Focus
- Race between repeated tick calls and concurrent arm/pause.
- Backward/missing timestamps and malformed forecast linkages.
- Market rollover while open and absent official settlement.
- Slippage/fee/unit precision for both UP and DOWN sides.
- Daily loss boundaries and invalid bookkeeping on restart.

### Task 1: Durable simulator engine and risk-state machine
**Files:** Create `src/kalshi_edge/trading/paper_scalper.py`, `tests/trading/test_paper_scalper.py`
- [ ] RED: test entry, take-profit, stop, hold, restart, blocked quotes, re-entry, ledger, capital limits and mock HTTP/public-only.
- [ ] GREEN: implement with SQLite transactions, idempotent tick and pessimistic quote fills.
- [ ] Verify full trading tests.

### Task 2: API/dashboard integration
**Files:** Modify `src/kalshi_edge/bootstrap/final_history.py`; create `src/kalshi_edge/bootstrap/scalp_paper_ui.py`; create `tests/bootstrap/test_paper_scalp_api_ui.py`.
- [ ] RED: assert isolated same-origin JSON actions, status, no real trading routes.
- [ ] GREEN: inject runner, safe routes and a separate paper-only panel.
- [ ] Verify API tests, full suite and production build.

### Task 3: Review/release
- [ ] Compare PR diff against design, no model/live-order changes.
- [ ] Merge green verified PR to main and provide exact local update command.
- [ ] Disclose that paper P/L is simulated and live scalping has not been enabled.
