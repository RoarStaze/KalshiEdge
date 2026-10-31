# KalshiEdge BTC 15-minute: research and improvement plan (2026-10-31)

## Executive finding

**Accuracy is not profitability.** The existing final-forecast gate requires model
`confidence >= 0.69`, and the early-entry AutoTrade gate requires `>= 0.90`,
but before this branch an automated BUY could execute without comparing its
exchange quote against the model-implied win probability *after fees*.

In the current live predictor, `confidence = 2 * abs(p_yes - 0.5)`. It is a
distance-from-50% score, **not** directly a win probability. The model-implied
win probability on the predicted side is `(1 + confidence) / 2`.
This equivalence is a source-code fact, not an independent calibration finding.

At a cost of $0.98 per share, even a **genuinely calibrated** 95% win chance
has negative expected value before fees. With 0.11 shares, expected gross
settlement is $0.1045 against a $0.1078 principal debit; the fee worsens it.
A model with excellent directional accuracy can therefore lose money when
its entry prices are too high.

## Implemented in this branch: live BUY execution value gate

Before exchange order journaling or POST, for AutoTrade BUY **only**:

1. Require a valid, current manual-trader quote containing unit price, filled
   contract quantity estimate, estimated fee, and estimated all-in debit.
   Missing, invalid, or contradictory numeric inputs fail closed.
2. Convert the current confidence score into the model-implied probability of
   winning, as defined by the live predictor.
3. Subtract 0.03 absolute probability points for conservative model uncertainty.
   **This haircut is a policy assumption, not a fitted confidence interval.**
4. Compute conservative **expected net dollars** as
   `(p_model_win - 0.03) * shares - max(preview_debit, price * shares + fee)`.
5. Require expected net dollars >= max($0.005, 2% of estimated debit).
   Both margins are **conservative policy assumptions, not optimized thresholds**.
6. For unfavorable *early* BUYs, wait and allow a new price/quote to be evaluated.
   For unfavorable *final* BUYs, mark the round SKIPPED. For continuous mode,
   advance to the next market. No order attempt is persisted, and no POST is sent.
7. Do not apply the BUY value gate to the risk-reducing reversal SELL.
8. Preserve all existing user-arm requirements, one-shot order journal,
   stale-signal checks, ticker verification, IOC semantics, and review flows.

**Important limitations:** The exchange's final execution fee can differ from
the quote estimate; price or liquidity may move. The underlying model
probability may be miscalibrated. A positive modeled EV **does not prove**
a trade is profitable, and this gate may reduce the number of filled trades.
The software must be judged by future *settled, executed, fee-inclusive*
outcomes, not the initial retrospective selection.

## Prioritized next improvements (NOT implemented here)

### 1. Independent, correctly timed evidence

- Preserve event-time receipt timestamps for BRTI, Kalshi prices/order books,
  and model outputs; reject inconsistent strike/settlement source alignment.
- Build a historical dataset of **decision-time, executable** YES/NO quotes,
  order book depth and actual fills; never use a settled price as a pre-entry
  quote and never assume all quoted quantity executes.
- Replay in strict chronological, ticker-grouped walk-forward splits with an
  embargo. Maintain untouched later-period lockboxes; do not repeatedly tune
  on the same outcomes.
- Report Brier score, log loss, reliability diagrams / ECE, observed conditional
  accuracy by time remaining and volatility, and matched-coverage baselines
  including Kalshi market-implied odds.

### 2. Reliable trading economics

- Separate forecast accuracy, *execution fill probability*, realized fees,
  canceled IOC shares, and net settled P/L. Count all no-fills and skipped
  markets, not just successful trades.
- Audit quote-to-order delay, slippage, spread and market impact. Compare
  shadow/live action at matched opportunities with all costs included.
- Monitor mean expected net gain vs **realized** net gain on disjoint future
  data. Freeze rollout / reduce activity if calibration drifts.
- Add account-level session loss stops, explicit stake caps and manual emergency
  stop after trusted realized P/L reconciliation is implemented and reviewed.
  Do not infer precise drawdown from missing or delayed settlement data.

### 3. More intelligent forecasts (only if out-of-sample superior)

- Compare existing V5 candidate, Kalshi market-implied prior, and V6 shadow
  against a transparent logistic/volatility baseline.
- Candidate features: causal BRTI strike distance, time to expiration,
  rolling BRTI realized volatility and jumps, Kalshi spread/depth/order flow,
  and feed staleness and inter-venue divergence.
- Try a regularized ensemble or gradient-boosted tabular learner only when
  features have enough unique settled markets; use leakage-free calibration
  within the train/dev portion.
- Promote only if candidate is no worse on Brier/log loss/calibration and
  economically superior **after simulated and live fees at matched coverage**
  over fresh, independent markets. Preserve existing model if evidence is weak.

### 4. Operational resilience

- Budget feed/feature/model/quote/authenticate/post timings separately.
- Compare BRTI authoritative strike with outside spot data, which can diverge;
  do not substitute Binance settlement for Kalshi BRTI.
- Monitor API 429s, staleness, missing order-book levels, time drift,
  connection dropouts, exchange halts, fill reporting and market rollover.
- Keep all changes read-only or paper/shadow until trade safety and model
  calibration are measured in a forward sample.

## Evaluation and release gate

1. Unit tests: invalid/partial quotes, misleading "high confidence", unprofitable
   fee-inclusive quotes, positive-edge quotes, changing ticker, SELL reversals.
2. Complete repo CI, including all existing trading-risk tests.
3. Read-only shadow audit on future live opportunities, **before** attributing
   any gain to the new gate.
4. No automatic stake increase, confidence threshold relaxation or model
   replacement on historical in-sample evidence.
5. Review results on samples that include at least several distinct market
   regimes, with confidence intervals and no claim of guaranteed return.

## Sources (read 2026-10-31)

- Kalshi 15-minute market dynamics: https://kalshi.com/pro/help/fifteen-minute-markets
- Kalshi trading fees: https://help.kalshi.com/en/articles/13823805-fees
- Kalshi API guide: https://help.kalshi.com/en/articles/13823854-kalshi-api
- Gneiting & Raftery, *Strictly Proper Scoring Rules, Prediction, and Estimation*:
  https://www.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf
- Gneiting, Balabdaoui & Raftery, *Probabilistic Forecasts, Calibration and
  Sharpness*: https://academic.oup.com/jrsssb/article/69/2/243/7109375
- Forecast evaluation and data leakage:
  https://link.springer.com/article/10.1007/s10618-022-00894-5
- Code evidence: `src/kalshi_edge/bootstrap/live.py`,
  `src/kalshi_edge/trading/armed_autotrade.py`, `trading/risk.py`,
  `bootstrap/evaluate.py`, and `bootstrap/threshold_audit.py`.
