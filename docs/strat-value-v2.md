# KalshiEdge Strat V2 — Settlement Value (PAPER ONLY)

**Replaces the active /strat page's failed intramarket momentum strategy.**
Old V1 implementation and `data/bootstrap/trading/strat_up_paper.sqlite3`
remain available for audit. V1 is disarmed; its background worker only
continues handling any previously open paper position through settlement.

## Research verdict

The earlier V1 quote-replay sample (173 BUY signals in the user-provided
dashboard) showed negative modeled taker net returns over every measured
2/5/10/20-second horizon. Changing exit timing did not establish profitable
entries. The following replacement is **more disciplined**, not **proven
more profitable**.

Kalshi's official BTC contract settlement explanation says a 15-minute
market compares the CF Benchmarks BRTI simple average over the minute before
the contract closes to the average before it opened.
https://help.kalshi.com/en/articles/13823838-crypto-markets

Default Kalshi standard taker fees are a rounded function of order quantity
and contract price; product/account specifics can change the fee.
https://kalshi.com/docs/kalshi-fee-schedule.pdf

Market microstructure research shows optimistic maker-fills, network latency,
and adverse selection can turn paper backtests into actual losses:
https://doi.org/10.1080/14697688.2025.2515933

## New trading policy

1. **Only the frozen 8-minute final forecast** with the correct ticker, close
   timestamp, 480s checkpoint, non-SKIP direction, model hash/version,
   matching UP/DOWN probability, and non-coin-flip classification qualifies.
   Forecast must be at most 30 seconds old and live predictor healthy.
2. **Positive after-cost expected value.** Require model side probability
   >=78%. Shrink the probability 25% toward 50% to account heuristically
   for model uncertainty. Compare to actual displayed executable best ask
   plus 1¢ estimated taker slippage, 1.5x the standard fee estimate and
   **an extra 5¢ safety buffer per contract**. Positive remaining margin
   is necessary. This shrinkage is *not* a statistically calibrated
   confidence bound and cannot certify profit.
3. **Quote integrity:** fresh receive timestamp, matched Kalshi market
   ticker, public market active/open, ask and bid consistent, spread <=3¢,
   positive displayed depth; verify public strike and close metadata
   against the frozen signal when available. Quote arrival time is not the
   exchange's original book-event timestamp. Displayed liquidity and
   market-specific fee assumptions are not guaranteed executable fills.
4. **Conservative exposure:** one hypothetical position **per ticker**,
   held to settlement, at most 1% current virtual cash per bet and $2
   accumulated same-day settled losses before new entries stop.
   User's maximum amount is $0.10–$2.00; actual stake is the smaller
   of this and the risk limit. No martingale and no profit-only exit.
5. **Official result integrity:** only a previously saved Kalshi public
   settled/finalized API result that passes local ticker, close, provenance
   and direction consistency checks may settle an open position. The
   separate question of independently reconstructing BRTI's exact
   one-minute settlement value has **not** been resolved here. Missing
   official outcome leaves the V2 holding OPEN, not a fabricated win.

## Operational steps

Run `kalshiedge start`, select **Strat**, set a paper amount, and click
**Arm value Strat**. It starts disarmed on every reboot. The existing
`/api/strat/status`, `/api/strat/arm`, and `/api/strat/pause` API
permissions remain localhost/same-origin. Both the UI and the backend
have **no live order submission capability**.

A missing verified model checkpoint, insufficient price advantage, wide
spread or a risk cap can cause **zero paper trades**. This is correct
behavior, not a reason to lower thresholds until it buys.

New trade and settlement records live in
`data/bootstrap/trading/strat_value_paper.sqlite3`. The old momentum
database `strat_up_paper.sqlite3` is not deleted or mixed into results.

## Validation requirement before any real-money deployment

Do **not** infer profitability from a favorable expected value formula,
the model's selected-trade win rate, a maker-fill counterfactual, or unit
tests. Conduct a frozen chronological holdout across independent BTC
15-minute markets matching actual 480-second orderbook snapshots to
Kalshi's published outcomes and a separate independent BRTI audit. Include
all real order-fills/partial fills, spread, order latency, account-specific
fees, losses, unresolved markets, drawdown, calibration, EV and coverage.
No historical backtest is claimed in this release: the user's full local
timestamp-aligned datasets were not accessible to this GitHub-only build.

**No profit or win-rate guarantees.**
