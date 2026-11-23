# Strat V2 — forecast-value settlement experiment (2026-11-22)

## Research finding / non-claim

The existing Strat's captured 173 BUY signals had **negative modeled after-cost
returns at 2, 5, 10 and 20 seconds**. The momentum burst detector is not a
demonstrated trading advantage. The previous profit-only slowdown exit also
could postpone/obscure losses until official settlement; it does not make a
losing entry profitable. Do not optimize synthetic replays to produce winners.

Kalshi's help center specifies that 15-minute BTC outcome depends on the
**one-minute CF Benchmarks BRTI averages at the start and close**, not the
instantaneous last Binance trade:
https://help.kalshi.com/en/articles/13823838-crypto-markets

Official Kalshi fee schedule (July 7, 2026) defines default taker fees as
rounded `M × 0.07 × C × P × (1−P)`, subject to product-specific changes:
https://kalshi.com/docs/kalshi-fee-schedule.pdf

Exchange microstructure research finds that limit queue fill estimates,
latency and adverse selection invalidate many optimistic HFT markouts:
https://doi.org/10.1080/14697688.2025.2515933
https://doi.org/10.1287/mnsc.2023.03371

## Design decision

Replace the *active* /strat page/worker with **V2 frozen-checkpoint, fee-aware
positive-value hold-to-locally-integrity-checked-Kalshi-API-settlement PAPER trading**. Preserve V1
StratPaperTrader/legacy UI and SQLite file intact for audit and regression.

1. Eligible at most once per KXBTC15M ticker; only within **30s of the sealed
   480s checkpoint**, not at arbitrary times during the round. No 5-tick
   momentum triggers or profit-only exit hacks.
2. Demand LIVE fresh predictor and internally consistent sealed final:
   correct ticker/close/checkpoint timestamp; forecast ABOVE or BELOW;
   model provenance; finite p_yes/p_no summing to 1; matching direction;
   LOW near-strike risk assessment; no stale or SKIP record.
3. Conservative decision: `p_guarded=0.5+0.75*(p_side−0.5)`. This is a
   *heuristic uncertainty haircut*, NOT a calibrated lower confidence bound.
   Demand original side p ≥0.78; compare p_guarded with executable best ask +
   1c slippage + modeled standard taker fee (also reserve 1.5× fee
   multiplicative uncertainty) + **5c per-contract risk buffer**. Price and
   depth must be valid, spread ≤3c. No apparent edge = SKIP. No implied profit.
4. Limit total hypothetical paid amount to min(user amount $0.10–$2.00,
   1% of starting/remaining virtual equity); max 1 outstanding position;
   at most one completed/entered position per ticker. Pause/new process
   disables new entry. If daily realized loss >=$2, stop new paper buys.
5. One hypothetical taker buy (actual fill not verified), hold through
   *verified* official settlement; no manufactured sale or assumed payout.
   If settlement unavailable, remain OPEN/WAIT; no settlement made up.
6. Store V2 trades in `data/bootstrap/trading/strat_value_paper.sqlite3`.
   Prior V1 `strat_up_paper.sqlite3` untouched. Clearly label V2 vs V1
   on the /strat page. Localhost and same-origin controls unchanged.
7. Replay/validation: status must label `NOT_VALIDATED_PROFITABLE`; report
   settled and unresolved positions, net P&L, and coverage. Do not call a
   positive expected-value *calculation* a proven positive realized return.
   Required future milestone: chronological holdout of actual checkpoint-time
   Kalshi order books joined to independently verified BRTI settlements,
   verified fill/fee evidence, and frozen policy audit.

## Implementation plan

1. RED tests for pure locked forecast and executable fee-aware value gate:
   calibration haircuts, freshness/market mismatch, status/near-strike,
   fee, spread and depth, quote timestamps and side consistency.
2. Implement `src/kalshi_edge/trading/strat_value_policy.py`.
3. RED worker tests: one trade per market, correct side/qty, read-only public
   quote, daily exposure brake, restart, verified settlement or unresolved
   ledger and preserved V1 ledger.
4. Implement `src/kalshi_edge/trading/strat_value.py`, using durable
   PaperScalpLedger and existing public market client; no auth clients.
5. Add `render_value_strat_page()` in `bootstrap/strat_ui.py`, switch
   /strat route and background worker in `final_history.py`. Keep original
   render_strat_page for backward-compatible tests.
6. Update runtime source fingerprint and docs; unit tests + full GitHub CI
   + container build; merge to main only if all pass. Supply PowerShell pull.

This release **cannot demonstrate consistency of profit** without the user's
local historical quote/forecast data and out-of-sample settlement evidence.
If no candidate qualifies, zero paper trades is correct behavior.
