# Actual AutoTrade execution quality: stored quote vs matched fill

**Read-only research** — no modification to the predictor, AutoTrade order sizing, confidence thresholds, exchange requests or authenticated account state.

## Why this matters

A high-confidence forecast can fail to produce profit if the executable contract price or fee has moved. Price forecasts and execution economics must be evaluated separately. The stored, decision-time quote snapshot from PR #67 enables a matched comparison against the actual Kalshi authenticated fills already used by the trusted P/L reconciliation.

## New audit output

The existing authenticated AutoTrade History response now includes `execution_quality` alongside each market's fee-inclusive `realized_pnl_dollars`, **only after** all market fills, order IDs, ticker, actions, sides, quantities and official settlement have reconciled. It does not issue additional exchange requests. The UI may not yet expose these fields; API clients can inspect the history response.

For each stage:
- `quoted_unit_price_dollars`: saved at quote/preview response time, not reconstructed.
- `actual_weighted_fill_price_dollars`: price-weighted executed YES or NO position price, from all matching fill records.
- `quote_count_fp`, `filled_count_fp`, and `fill_fraction_of_quote`: explicitly distinguish partially executed orders from quotes.
- `estimated_quote_fee_per_contract_dollars`: preview fee estimate divided by quoted shares (a normalization assumption, not a reconstructed exact Kalshi fee schedule).
- `actual_fill_fee_per_contract_dollars`: total verified fee_cost divided by actual filled shares.
- `adverse_all_in_cost_per_contract_dollars`: positive for a higher-cost BUY or lower-net-proceeds reversal SELL; negative for better execution.
- `adverse_total_for_filled_shares_dollars`: per-share adverse cost multiplied by **only the actually filled quantity**, not unfilled quoted shares.

### Formula

For `side` = YES/UP or NO/DOWN, take the quoted price of that same contract side.

```text
actual_avg_fill = sum(fill_count * side_fill_price) / sum(fill_count)
actual_fee_per_share = sum(fill_fee_cost) / sum(fill_count)
estimated_fee_per_share = quote_fee_estimate / quoted_share_count

BUY adverse_per_share =
    actual_avg_fill - quoted_side_buy_price
    + actual_fee_per_share - estimated_fee_per_share

SELL adverse_per_share =
    quoted_side_sell_price - actual_avg_fill
    + actual_fee_per_share - estimated_fee_per_share
```

A quote for more shares than filled uses the original estimated fee **per quoted share** and never assigns execution to canceled shares.

## Status and safeguards

- `VERIFIED_QUOTE_TO_FILL`: all executed stage quotes are available and match the verified fills.
- `PARTIAL_QUOTE_COVERAGE`: at least one stage has a matched quote, and at least one has no comparable quote; the market-wide adverse total stays NULL.
- `MISSING_QUOTE`: older stage records do not have archived quote data; *not* zero slippage.
- `UNAVAILABLE`: an incompatible quote, fill, market, order, side, action or quantity prevents trusted execution-quality measurement.
- `execution_quality=null`: market settlement/fill reconciliation not verified. This always remains distinct from a market's profit.

These calculations do not modify trusted realized P/L, change the fact that an IOC can partially fill, infer a price for missing stages, alter live risk limits, or place orders.

## What it does *not* prove

- No counterfactual about what price would have been available if an order had been sent earlier or later.
- No causal estimate of market impact, informed trading, or the execution quality of rejected signals.
- No true latency metric until the exchange fill's timestamp and local pre-post execution timing are reliably linked.
- No generalized profitability or improvement in the AI's forecasting accuracy; we will need prospective matched economic evaluations to make those claims.

## Next engineering step

After new AutoTrade orders accrue with PR #67's quote snapshots, analyze the conditional distribution of actual adverse execution cost and the confidence-dependent net P/L. Report sample size and missing-data coverage before testing any strategy changes. Keep stake sizes and confidence gates fixed during collection.

## Sources

- Kalshi authenticated fill fields: https://docs.kalshi.com/api-reference/portfolio/get-fills
- Kalshi fee variation: https://help.kalshi.com/en/articles/13823805-fees
- Gneiting et al. (2007), *Probabilistic Forecasts, Calibration and Sharpness*: https://academic.oup.com/jrsssb/article/69/2/243/7109375
