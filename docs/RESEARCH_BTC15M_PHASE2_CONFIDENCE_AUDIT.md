# KXBTC15M Phase 2 — Research and verified model-evaluation definitions

**As of:** 2026-10-31  
**Status:** Read-only measurement improvements; no new model or threshold has been promoted.

## 1. Root cause: confidence and probability were conflated

The live predictor defines `confidence = 2 * abs(p_yes - 0.5)`.
The selected-side model probability is `max(p_yes, 1-p_yes)`.

The earlier retrospective threshold audit compared `max(p_yes,p_no)`
to threshold values that in the live program refer to *confidence*.
That made its coverage and directional-accuracy figures incomparable to the
production final/early gates. This change fixes the audit to use the live
formula without changing any live order or prediction.

| Live confidence threshold | Selected-side model probability |
| --- | --- |
| 0.69 | 0.845 |
| 0.75 | 0.875 |
| 0.80 | 0.900 |
| 0.90 | 0.950 |
| 0.95 | 0.975 |

**Neither confidence nor model-implied probability is an independently
verified real-world win frequency.** The production fee-aware BUY gate introduced
earlier uses a policy haircut that is not a statistically fitted calibration.

## 2. New reproducible analysis commands

From the KalshiEdge environment after synchronizing source:

```powershell
python -m kalshi_edge.bootstrap.threshold_audit
python -m kalshi_edge.bootstrap.forward_monitor
```

These commands read the saved forecast and official settlement artifacts from
the configured bootstrap directory. They **never send orders**, retrain models,
change stake sizes, change final forecasts, or select trading thresholds.

The reliability monitor:

- scores only officially settled, finite, coherent, ticker-unique forecasts;
- includes otherwise-valid sub-threshold SKIP forecasts for probability
  diagnostics, but excludes feed-failure/time-mismatch skips;
- excludes *all* duplicates of a settled ticker rather than retaining whichever
  duplicate was more favorable;
- reports accuracy, Brier score, log loss, expected calibration error (ECE),
  and 10 reliability bins;
- reports cumulative confidence cohorts with mean model-implied win probability,
  observed win rate, 95% Wilson lower bound, and gap;
- separates model versions and chronological last-50 / last-200 market windows;
- requires a configurable minimum descriptive sample (100 by default) before
  reporting `DESCRIPTIVE_ONLY`, never a model-promotion permission.

**Limitations:** A cohort may contain too few markets to be informative.
The rolling windows are descriptive and overlap. Historical data was
available during development, so this report is NOT an independent lockbox.
A 95% Wilson interval is a binomial descriptive approximation and does
not establish independence of adjacent 15-minute markets.

## 3. Priorities for the next safe stage

1. **Forward shadow evaluation:** Collect future market outcomes *after* code
   freeze. Track both model and live Kalshi market-implied probability at each
   immutable checkpoint; use decision-time rather than final prices.
2. **Proper forecasting comparison:** On a separate prospective sample,
   compare candidate vs prior and current promoted model on Brier, log loss,
   calibration, and accuracy at *matched trade coverage*.
3. **Executable economic replay:** Record timestamped YES/NO bid/ask, depth,
   actual fills/cancellations, fee estimates, and realized net P/L. No trade
   price or fee assumption should be relabeled a verified historical fill.
4. **Risk controls:** Add account-level realized-loss stops and pending
   exposure checks only once same-day settlements and fill reconciliation
   can be trusted. Do not derive daily loss from cash balance changes alone.
5. **Candidate AI modeling:** On strictly earlier training periods, compare
   calibrated regularized models, BRTI price/strike/time/volatility features,
   and complementary Kalshi order-book information. Require an untouched,
   later-market validation block and no feature timestamp leakage before
   production promotion.

The specific KXBTC15M contract terms control settlement. Kalshi describes
15-minute crypto markets as comparing minute averages near the open and close,
based on a designated CF Benchmarks index, not necessarily a single Binance
spot observation.

## 4. Immutable decision-time Kalshi market benchmark (added in Phase 3)

Every *valid* 240-second locked forecast now retains the synchronous YES bid and
ask displayed at model inference time. This is the only defensible market
reference for *subsequent* matched comparison: archived bid/ask values must not
be replaced with post-settlement odds. Legacy forecasts retain null bid/ask;
they are never backfilled or inferred from a winning market.

`python -m kalshi_edge.bootstrap.forward_monitor` now reports
`market_midpoint_comparison` and `market_midpoint_by_model`. It compares the
Brier and log-loss scores of the AI and `(yes_bid+yes_ask)/2` on *identical*
settled markets with validated two-sided quotes, and reports coverage and
mean observed spread. A positive Brier improvement is descriptive evidence
that AI probability forecasts fit the observed outcome better on that
matched subset—not a guarantee of out-of-sample superiority or live profits.

The bid/ask midpoint cannot be executed and may include liquidity and
risk-preference effects. Estimate net trade value separately using
side-specific executable ask, quote depth, actual fees, and real fills.
The right next experiment is prospectively collecting fresh, never-inspected
markets and comparing with stable methods under a predeclared evaluation
rule before promoting a new model.

## Sources

- Kalshi Crypto Markets (settlement methodology): https://help.kalshi.com/en/articles/13823838-crypto-markets
- Kalshi API fills (quantities, fees, time, pagination): https://docs.kalshi.com/api-reference/portfolio/get-fills
- Gneiting et al. 2007, *Probabilistic Forecasts, Calibration and Sharpness*:
  https://academic.oup.com/jrsssb/article/69/2/243/7109375
- Classifier calibration survey (Brier/log loss and recalibration methods):
  https://link.springer.com/article/10.1007/s10994-023-06336-7
- KalshiEdge source:
  `src/kalshi_edge/bootstrap/live.py`,
  `src/kalshi_edge/bootstrap/final_forecast.py`,
  `src/kalshi_edge/bootstrap/threshold_audit.py`,
  `src/kalshi_edge/bootstrap/forward_monitor.py`.
