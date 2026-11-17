# KalshiEdge fee-aware intramarket scalping: design and evidence gate

## Objective
Permit a position to be closed before settlement and potentially repeat small,
independently advantageous round trips. Never claim profitable scalps without
real time-ordered, market-specific executable prices, fees, fills, and risk data.

## Verified current implementation (repository main at 82a4518)
- `ArmedAutoTrader` enters once per KXBTC15M market at the immutable 480-second
  final with a strict >69% predicted-direction-probability gate. Its state
  terminates after FILLED/PARTIAL. Current SQLite stage schema is not a
  multi-cycle inventory ledger. The current AutoTrade has no automatic exit.
- `ManualTrader` supports user-triggered BUY and SELL and sends IOC with
  `reduce_only` for SELL. Sell preview rechecks open market, available position,
  fresh executable book and limit. An unknown POST response is never retried.
- `autotrade_pnl` reconciles matching orders, fills and settlements. It must
  gain complete early-close order-and-fee accounting before live scalping.
- The existing predictor's historical evaluation is a 480-second **settlement**
  forecast, not a forward return model for a 5–180-second market-price move.

## Primary sources
- Kalshi Get Market Orderbook:
  https://docs.kalshi.com/api-reference/market/get-market-orderbook
  REST returns YES and NO bids only. The executable ask for YES is
  `1 - best NO bid`; the executable ask for NO is `1 - best YES bid`.
- Kalshi Order direction:
  https://docs.kalshi.com/getting_started/order_direction
  V2 `outcome_side` and `book_side` are canonical; old action/side are
  deprecated. On WS orderbook deltas, no-leg prices may be on a different scale
  unless `use_yes_price` is explicitly set.
- Kalshi Create Order V2:
  https://docs.kalshi.com/api-reference/orders/create-order-v2
  `reduce_only` is available only for IOC orders. Submission is not
  confirmation of execution, and an IOC can partially fill or not fill.
- Kalshi current fee schedule: https://kalshi.com/fee-schedule.
  Market-specific overrides can differ; project fee reserves are indicative.
- Aït-Sahalia & Sağlam (2024), Journal of Econometrics,
  doi:10.1016/j.jeconom.2022.12.015. Speed, spread and adverse-selection
  tradeoffs in high-frequency market-making. Not empirical proof for Kalshi
  scalping or for any stop-loss parameter.

## Considered designs
1. Fixed percent stop on last trade or midpoint — rejected. Quotes are not
   executable bids and costs can exceed small target profits.
2. Fee-aware, depth-aware policy based on executable best bids, holding-period,
   realized entry debit, loss budget, and trailing drawdown — chosen as the
   deterministic research component. Stops are **signals**, not guaranteed fills.
3. Automatic market-maker quoting — deferred. It requires queue-position
   models, cancel/requote, adversarial fill testing, and session recovery.

## Risk policy — research hypotheses, NOT optimized settings
- Entry must have immutable matching final above >69% as a safety filter,
  independently fresh market book depth and momentum. A final-settlement model
  probability is not a short-horizon price forecast.
- Compute spread as executable best ask minus executable best bid **for the
  purchased outcome**; include estimated non-direct entry and exit fees.
- Never infer profits from midpoint, last trade, strike movement, forecast
  confidence or unfilled resting quotes.
- A USD loss budget, time stop, trailing stop and net executable profit target
  operate on *verified inventory and actual entry debit*. Stops cannot guarantee
  liquidation during low liquidity, halted markets or network failures.
- Refuse stale, crossed, malformed or insufficient-depth books. A partial exit
  leaves an open inventory obligation. Unknown results halt automatic retries.
- No martingale, exposure averaging, side switching, or uncontrolled re-entry.

## Evidence and production release gates
**Gate A: baseline feed evidence**. Immutable point-in-time L2 snapshots,
sequence gaps, quote age, authentic order IDs, YES/NO fill prices, fees,
partial fills, settlement and account positions must be recoverable. Current
31k-market historical classification dataset is not sufficient evidence of
intramarket executable exits by itself.

**Gate B: independent chronological simulator**. Replay a frozen policy using
top-of-book/depth, realistic queue or market-taking fill assumptions, fee
overrides, latency, partial fills, slippage, and no-lookahead execution. Report
net P/L distribution, turnover, capture rate, fill-rate, time in market, drawdown,
adverse selection and confidence intervals. If fills or historical books are
missing, the result is `INSUFFICIENT_EXECUTION_EVIDENCE`, not profits.

**Gate C: prospective paper and permission**. Independent paper observations
and user opt-in with explicit dollar budget, daily stop, max position, no
automatic live deployment, and an emergency halt. No claim of expected returns.

**Gate D: production execution**. Dedicated durable `scalp_cycle_id` journal;
serial order state transitions; reduce-only IOC exit; mark-to-liquidate using
actual fills and fresh account holdings; restarts resume risk monitoring before
any new BUY; no duplicate POST/retry on UNKNOWN; settle or close cycles using
all fee-inclusive authenticated fills. Monitoring continues while exposure is
open; pausing new entries does not silently abandon open positions.

This PR implements the read-only core of Gate A/B decision logic. It MUST NOT
be described as a deployed live scalper, a validated strategy, or a guaranteed
stop-loss.
