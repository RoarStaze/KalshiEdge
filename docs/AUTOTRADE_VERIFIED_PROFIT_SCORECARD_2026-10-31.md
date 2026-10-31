# AutoTrade realized profitability: verified scorecard and fill integrity

**Date:** 2026-10-31  
**Scope:** Verification and observability only. This change does not increase
stake size, re-arm AutoTrade, relax an order gate, or place any exchange order.

## Why this matters

A profitable forecast is not necessarily a profitable trade. Profit requires
recovering the *actual* executed position from exchange fills, its real fees,
and its settlement—not counting prediction correctness or a theoretical payout.

Kalshi's current authenticated fill API documents `order_id`, `ticker`,
`count_fp`, `yes_price_dollars`, `no_price_dollars`, `fee_cost`, `side`
(yes/no) and `action` (buy/sell). The settlement API documents market outcome.
These must agree with the locally journaled stage action/side before a
numerical exact-profit value is published.

## Implemented

1. **Fail-closed reconciliation:** A reported fill with a different order's
   action, YES/NO contract side, or ticker cannot be attributed as confirmed
   AutoTrade profit. Missing required identifiers also blocks a numeric result.
2. **Read-only recent scorecard:** The AUTO history response now includes
   a `summary` of *already verified* settled P/L. It uses Decimal arithmetic
   on fixed-point strings, and reports gains, losses, breakeven, filled markets
   without verified P/L, no fills, duplicates excluded and the number of
   markets in the bounded local history.
3. **History UI:** Shows green/red verified net P/L for the **recent journal
   window only**, with a separate completeness note. Unsettled, mixed,
   missing, invalid, or unknown outcomes are excluded from this numeric total.
4. **No invented ROI:** It does not claim lifetime P/L, performance on other
   accounts, trading ROI, return per dollar at risk, or future profitability.
   A market is not counted twice, and missing results are not counted as $0.

## Source limitations and next experiments

- `recent()` currently returns at most 25 markets by default; older trades
  are not part of the scorecard. Do not label it daily or lifetime profit.
- Kalshi can delay official settlement. Unresolved markets remain unknown,
  not losses; settlement fees/aggregated position semantics must be verified
  before any aggregate cash-flow claim.
- Account balance changes are not a return calculation; deposits, withdrawals,
  manual trades, fees and unrealized positions change balance.
- To measure expected-value skill, prospectively log the model probability,
  contemporaneous executable ask and depth, estimated fees, and decision
  timestamp for **every candidate** (including rejected and no-fill cases),
  then join to filled executions and settlements. Compare live realized net
  performance against the frozen baseline and ensure no future information
  enters the decision signal.
- After a predeclared observation period across different BTC volatility
  regimes, evaluate confidence calibration, Brier/log loss, matched-decision
  returns and drawdowns. Only then consider new live model weights,
  dynamic bet sizing or configurable loss limits.

## References

- Kalshi fill API: https://docs.kalshi.com/api-reference/portfolio/get-fills
- Kalshi settlements: https://docs.kalshi.com/api-reference/portfolio/get-settlements
- Kalshi token budgeting/rate limits:
  https://docs.kalshi.com/getting_started/rate_limits
- Gneiting, Balabdaoui, Raftery (2007) Probabilistic Forecasts, Calibration
  and Sharpness: https://academic.oup.com/jrsssb/article/69/2/243/7109375
- Diebold/Mariano and sequential model comparison reviewed in:
  https://academic.oup.com/biomet/article/109/3/647/6375942

## Manual validation

Run repository CI on the PR with synthetic local exchange responses.
After merging and syncing on the user's computer, visually verify the
history summary matches the explicit sum of *verified* per-market P/L.
If any recorded order is unavailable, show partial coverage, not a
rounded-up or fabricated profit. Live account access was NOT available
during automated testing; no live profitability claim is made.
