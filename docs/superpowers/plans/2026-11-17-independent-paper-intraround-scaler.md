# Independent Paper Intraround Scalper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the paper scalper's 480-second final gate with continuous UP/DOWN orderbook momentum, fee-aware prompt exit and the existing safe simulated ledger.

**Architecture:** Keep live AutoTrade untouched. Extend `scalp_policy.py` with a separate `intraround_entry_signal` pure function. Adapt `PaperScalpTrader` to observe one public orderbook and both directional quotes, enforce fresh live market identity, select nonambiguous signed momentum with conservative fee/slippage costs, preserve existing exit and persistence lifecycle. Update paper-only panel status.

**Tech Stack:** Python 3.11, Decimal, sqlite3, pytest, existing localhost dashboard JS.

**Spec:** `docs/superpowers/specs/2026-11-17-independent-paper-intraround-scaler.md`

## Global Constraints
- No signed API, credentials, live order POST, real-money gate or model modification.
- No 480s final gate or model-confidence requirement for paper entries.
- No manufactured realized P/L, bid-ask crossing, stale quote acceptance or guaranteed stop.
- 3-second REST sampling is a data limitation, not a high-frequency guarantee.

## Review Focus
- Opposite UP/DOWN book trends cannot be confused with simple spread narrowing.
- All positions sized and evaluated including two-sided 1¢ modeled slippage and fees.
- A stale live market identity must disable **new** paper entries, not exits for open positions.
- Near-close and market rollover skip entries while open position still monitored safely.
- Multiple qualifying sides reject ambiguity; no auto-switch or duplicate post.

### Task 1: Independent pure short-horizon candidate policy
**Files:** Modify `src/kalshi_edge/trading/scalp_policy.py`; `tests/trading/test_scalp_policy.py`.
- [ ] Add RED tests for each side's rolling executable bid+ask movement, depth/crossed spread, stop-vs-cost rejection, before-final timing and no model argument.
- [ ] Implement `intraround_entry_signal(history, contracts, seconds_remaining)` with paper-only `RESEARCH_BUY_SIGNAL`, no confidence input.
- [ ] Extend `exit_signal(..., paper_minimum_net_profit=None)` to support fee-adjusted paper net>=1¢; preserve existing exit defaults and stop/trailing/time semantics.
- [ ] Verify targeted and whole trading tests.

### Task 2: Paper worker scans both sides
**Files:** Modify `src/kalshi_edge/trading/paper_scalper.py`; `tests/trading/test_paper_scalper.py`.
- [ ] RED: test prefinal opening, opposite direction, flat-book/no forecast, stale feed, no liquidity and ambiguity, repeat cycles, restart and no live orders.
- [ ] Replace `_live_entry_side` with fresh-market-only context, fetch book once per 3-second poll, record UP/DOWN quote histories separately, use independent policy, apply size + simulated 1¢ each side and choose only nonambiguous candidate.
- [ ] Pass 1¢ paper net profit to existing exit decisions; preserve paper stop, no-depth blocks, settlement, daily losses, pause and restart.
- [ ] Increase bounded paper cycle cap to 12 per 15m market. Preserve unique open journal index.
- [ ] Verify targeted and full trading suite.

### Task 3: Make the paper UI explain scanning rather than 69% final
**Files:** Modify `src/kalshi_edge/bootstrap/scalp_paper_ui.py`; `tests/bootstrap/test_paper_scalp_api_ui.py`.
- [ ] RED: assert no final forecast copy in paper panel; display scanner policy and no-profit guarantee.
- [ ] Show independent UP+DOWN paper scanner, 3-second sampling and >=1¢ **net** simulated exit indicator.
- [ ] Verify localhost UI/API and JavaScript syntax.

### Task 4: Release
- [ ] Run full GitHub Actions tests, full app/build.
- [ ] Verify only paper paths changed; no live AutoTrade modifications.
- [ ] Merge to main and provide local PowerShell update commands with explicit paper-only limitation.
