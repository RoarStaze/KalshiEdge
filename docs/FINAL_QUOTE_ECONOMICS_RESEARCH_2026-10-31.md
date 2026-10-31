# Decision-time final-checkpoint quote economics (research-only)

## Why
KalshiEdge's 15-minute BTC forecasts have demonstrated retrospective
directional accuracy, but direction accuracy **does not equal** economic
profitability: a correctly predicted YES at a 99¢ ask has a maximum gross
gain of 1¢ per hypothetical contract, before costs.

KalshiEdge already archives (1) immutable four-minute-before-close forecasts,
(2) the contemporaneous two-sided YES bid/ask on new forecast records, and
(3) a separately reconciled *official* market settlement. This command
reuses those artifacts and never touches AutoTrade's authenticated client.

## Run while the main application stays running

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

python -m kalshi_edge.bootstrap.quote_economics
python -m kalshi_edge.bootstrap.quote_economics --assumed-fee-per-contract 0.01
python -m kalshi_edge.bootstrap.quote_economics --recent 0
```

The default uses a $0 **assumed** fee because no real exchange fee can be
reconstructed from public final-checkpoint bid/ask alone. A custom fee is
a fixed stress-test assumption, **not** Kalshi's actual price-dependent
trading fee. The summary always includes every available local record,
whereas the CLI displays at most 20 opportunity rows by default.

## Calculations (one *hypothetical* contract per market)

| Contract | Quoted purchase price | Model-implied gross edge |
| --- | --- | --- |
| YES/ABOVE | checkpoint YES ask | P(YES) - YES ask |
| NO/BELOW | 1 - checkpoint YES bid | P(NO) - NO ask |

For an **officially settled** forecast with valid quote:

```text
hypothetical_gross_settlement_per_contract =
    (1 if selected side won else 0) - quoted_side_ask

model_net_edge_after_assumed_fee =
    model_gross_edge - specified_hypothetical_fee

hypothetical_net_settlement_after_assumed_fee =
    hypothetical_gross_settlement - specified_hypothetical_fee
```

A `SKIP` caused **specifically** by
`CONFIDENCE_BELOW_69_PERCENT` can be included as
`SKIPPED_LOW_CONFIDENCE` counterfactual data, but its settlement is
**never included** in directional forecast realized-value totals.
Feed-invalid SKIPs are not assumed to have valid model signals.

## Data quality rules

- Reject missing/nonfinite/out-of-range/crossed bid and ask.
- Reject missing/invalid/complement-inconsistent `p_yes` / `p_no`.
- Exclude **all** duplicate rows of a ticker rather than picking the better result.
- Leave pending settlements unscored; do not infer an outcome.
- Use **only** the original immutable quote, never a later or reconstructed price.
- Include no actual account cash, fills, P/L, or execution events.
- Show an explicit unknown result rather than $0 when there are zero eligible settled forecasts.

## Interpretation limits

These are **not actual fills or guaranteed executable orders**. They use
top-of-book quotes without available depth, quantity, price movement,
latency, maker/taker fee schedule, commissions, rebates, canceled IOC
shares, or market impact. Summing one hypothetical contract per market
is neither a trading backtest nor an actual portfolio return. It also
omits the early-entry AutoTrade path, which executes before final lock
and can subsequently reverse.

The next safe phase is a *separate* event-time audit of every AutoTrade
decision at its true quote/preview timestamp (including early entries,
reversals, no-fills and all skips), linked to authenticated execution
fills and fees, with conservative accounting and no credential leakage.
Only then can the program test a prospective value signal against
actual realizable net profits. Do not optimize models, stakes, or entry
thresholds against repeatedly inspected historical rounds.

## Primary sources

- Kalshi, Fees (updated April 2026):
  https://help.kalshi.com/en/articles/13823805-fees
- Kalshi, Quick Order Purchase (2026): orderbook depth may cause
  a single purchase to fill across multiple price levels.
  https://help.kalshi.com/en/articles/13823812-quick-order-purchase
- Kalshi, Prices and market probabilities:
  https://help.kalshi.com/en/articles/13823836-how-are-prices-determined
