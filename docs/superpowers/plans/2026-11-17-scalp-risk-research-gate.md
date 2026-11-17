# Fee-aware scalping research gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a verified, read-only executable-quote entry/exit policy on the current AutoTrade code path without putting an unvalidated scalper into real-money production.

**Architecture:** A standalone pure module owns quote normalization and deterministic risk evaluation. Existing AutoTrade status reports explicitly whether live scalping is enabled. No exchange POST or persistent model changes are introduced.

**Tech Stack:** Python 3.11+, Decimal, dataclasses, pytest; existing KalshiEdge ManualTrader reference fee reserves.

**Spec:** `docs/superpowers/specs/2026-11-17-scalp-risk-research-gate.md`

## Global Constraints
- Current 480s immutable final forecast and strict >69% live gate unchanged.
- No additional real orders; no P/L on unverified fills.
- Quotes only from YES/NO bids and valid sizes; refuse stale/inverted books.
- Represent stop-loss as an action signal, NEVER as a guaranteed max loss.
- Historical 480s classifier accuracy is not a backtested scalp edge.

## Review Focus
- Missing YES or NO bid: skip entry and require fresh evidence before exit.
- Bid/ask crossed due to malformed/stale book: refuse decisions.
- Quote depth less than owned shares: do not mark a full liquidation possible.
- Fees and fractional quantity rounding: calculate with fixed-point decimals.
- A halted market or no sell liquidity: do not invent a stop execution.

### Task 1: Pure executable price/risk policy
**Files:**
- Create: `src/kalshi_edge/trading/scalp_policy.py`
- Test: `tests/trading/test_scalp_policy.py`

**Interfaces:**
- `quote_from_kalshi(book: dict, side: str, *, timestamp_ns: int) -> ScalpQuote`
- `entry_signal(history: Sequence[ScalpQuote], probability, quantity, seconds_remaining) -> ScalpDecision`
- `exit_signal(position: ScalpPosition, quote: ScalpQuote, *, now_ns: int, seconds_remaining: int) -> ScalpDecision`
- `readiness() -> dict`

- [x] Write a failing test for executable yes/no prices, live-ineligible entries and exits, unsafe liquidity, fees and stop.
- [ ] Run GitHub CI and confirm the test fails because module is missing.
- [ ] Implement the minimal standalone pure policy.
- [ ] Run GitHub CI and verify tests pass.

### Task 2: Production-mode visibility and safe defaults
**Files:**
- Modify: `src/kalshi_edge/trading/armed_autotrade.py` status only
- Test: `tests/trading/test_scalp_policy.py`

- [ ] Assert AutoTrade reports scalp status as `RESEARCH_ONLY` with `live_orders_enabled=false`.
- [ ] Implement read-only status report; do not change arm, final BUY or executor.
- [ ] Run full pytest and production container CI.

### Task 3: Release discipline
**Files:** This spec and plan; PR notes.

- [ ] Independently inspect the PR diff for live POST changes and unsafe defaults.
- [ ] Confirm no new sell/entry orders or synthetic realized P/L.
- [ ] Merge to `main` only after all CI checks pass; supply local PowerShell update commands.
- [ ] Report that true live scalping remains blocked by absence of validated execution evidence.

**Next action:** Verify the test-first CI failure, then implement Task 1.
