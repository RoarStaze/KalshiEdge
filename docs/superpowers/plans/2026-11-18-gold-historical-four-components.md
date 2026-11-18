# GoldPredict V1.4 — historical research pipeline and four-component research architecture

**Purpose:** Evaluate XGBoost, a lagged time-series learner, macro/financial-language context, and market regime features using past gold data, without misrepresenting the historical market outcomes as independently verified Pyth settlement labels or silently deploying a model.

**Known inputs:** SHA-256-named Kalshi KXGOLD15M inventory artifacts, existing validated local Pyth one-minute OHLC imports, prospective quote receipts (optional), and verified observation-available-time macro headlines (optional). Existing gold dataset readiness audits remain **blocked (zero production-eligible labels)** until exact market terms and authenticated Pyth settlement alignment are independently established.

**Decision rule for research:** An explicit *assumed* 8-minute checkpoint (market close - 480 s), and bars whose minute end is at least an extra 60 s earlier. The minute boundaries and market close mapping are unverified by current gold contract evidence. Never use retrospective Kalshi YES prices as point-in-time features.

## Implementation tasks

1. **Historical source retrieval:** Provide bounded authenticated Pyth Pro OHLC download with the key exclusively in environment variables, no persistence of bearer secret, strict endpoint/channel/symbol/time validation, existing immutable hash-named evidence imports, no live execution.
2. **Research-only market reconstruction:** Join inventories and Pyth bars using explicit close-time assumption, constrain features to pre-checkpoint bars with an additional lag, reject ticker/source overlap and gaps, and retain Kalshi-reported labels *marked provisional*. Emit an auditable hash-addressed artifact, report exclusions and the assumption set, never set production readiness true.
3. **As-of macro adapter:** Require each headline's published and captured timestamps to precede prediction. Expose a local-only optional FinBERT adapter; do not download or score later-revised headlines as though known in the past. Missing macro evidence is a missing signal, not zero sentiment.
4. **Model comparisons:** Chronologically split and purge by training-label visibility assumption, fit XGBoost, a lagged-return time-series classifier, and train-only regime detection as a contextual feature. Compare a quote-market baseline and combined model on a held-out late period with accuracy/Brier/log-loss/coverage, without activation or saved deployable weights.
5. **Regression gates:** Test late-leaking bars, labels and macro rows, source hash mismatch, duplicate/conflicting outcomes, missing Pyth time coverage, model splits, no credentials in artifacts, no trade paths; run full CI and only merge on green.
6. **Explicit next gate:** Independently verify contract start/end candles, exact oracle channel/rounding rules, and archived data origin before publishing training-ready gold labels or promoting any model to trading.

**Non-goals:** Change BTC predictor or AutoTrade, initiate orders, claim authenticated settlement from Kalshi result alone, publish profitability, or silently install/download a financial language model.
