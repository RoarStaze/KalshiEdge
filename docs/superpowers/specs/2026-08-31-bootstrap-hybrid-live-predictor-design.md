# KXBTC15M Bootstrap Hybrid Live Predictor Design

## Status

Approved architecture for a parallel, read-only predictor that can be trained from already-available historical data and used before the Phase 1 first-party collector has accumulated enough data for a collector-native research model.

This subsystem is deliberately separate from the Phase 1 collector. It must not change, stop, overwrite, compact, reinterpret, or weaken the canonical collector data path or its acceptance gate.

## Objective

Build the strongest defensible KXBTC15M ABOVE/BELOW probability engine that can be trained and validated now from existing historical sources, while the existing production collector continues accumulating exact point-in-time Kalshi/BRTI data.

The live output is a calibrated probability of the active KXBTC15M market resolving YES/ABOVE versus NO/BELOW, plus transparent component probabilities, uncertainty, feed-quality state, and comparison with the executable Kalshi market.

The bootstrap predictor is not automatically evidence of a tradeable edge. It may be used immediately as a read-only predictor, but any claim that it beats the market must come from untouched chronological validation against the Kalshi market benchmark and executable economics.

## Non-negotiable boundaries

1. **Collector isolation.** Existing `kalshi-edge collect` behavior and `data/raw` canonical collection remain unchanged.
2. **Read only.** No order-placement, cancellation, portfolio mutation, or trading authorization is added.
3. **No gate bypass.** The Phase 1 empirical gate remains intact and Phase 2 is not declared passed merely because this predictor exists.
4. **No future leakage.** A feature may be used at timestamp `t` only if it was observable by `t` in the historical replay being constructed.
5. **Market-level chronological validation.** Snapshots from one market must never be split across train and validation/test sets.
6. **Exact labels first.** Kalshi's historical `result` and `settlement_value_dollars` are the authoritative labels when available; external BTC prices are features/proxies, never replacements for the official outcome label.
7. **Benchmark against Kalshi.** A model that does not improve out-of-sample probability quality over the contemporaneous Kalshi market is not represented as superior.
8. **No silent train/serve skew.** Historical and live feature definitions must record their source, timestamp availability, aggregation interval, and staleness.
9. **Provenance.** Every downloaded raw file and model artifact receives source metadata and a content hash.
10. **Fail closed on stale or inconsistent live inputs.** The predictor must emit `NO_PREDICTION` rather than manufacture confidence.

## Verified source capabilities

The implementation should re-check current official documentation at execution time, but the following capabilities were verified on 2026-08-31.

### Kalshi historical market labels

`GET /trade-api/v2/historical/markets/{ticker}` exposes historical market metadata including `result`, `settlement_value_dollars`, settlement timestamp, strike fields, market timestamps, liquidity, volume, and open interest. These fields provide exact market labels without reconstructing the settlement from a third-party BTC venue.

### Kalshi historical trades

`GET /trade-api/v2/historical/trades` can be filtered by ticker and timestamp and exposes timestamped `yes_price_dollars`, `no_price_dollars`, size, taker outcome/book side, and trade ID. This supports point-in-time traded probability, flow, momentum, and aggressive-side features.

### Kalshi historical market candlesticks

`GET /trade-api/v2/historical/markets/{ticker}/candlesticks` supports 1-minute candles and includes YES bid OHLC, YES ask OHLC, traded-price OHLC/mean/previous, volume, and open interest. It is the preferred first-party historical quote source where full historical order-book depth is unavailable.

### Kalshi historical cutoff routing

The backfill must query Kalshi's historical cutoff timestamps and route data to the historical versus current endpoints correctly rather than assuming all dates live behind one endpoint family.

### Binance official public archive

Binance's official public-data archive provides downloadable spot and futures klines/trades/aggregate trades, including 1-second spot klines and checksums. For spot data from 2025 onward, timestamps are microseconds and must be normalized explicitly. BTCUSDT spot is the primary freely available high-frequency external path source; USD-M BTCUSDT may be admitted as a supplemental feature family only after out-of-sample ablation shows incremental value.

### CF Benchmarks / BRTI

Live BRTI from the existing Kalshi collector is the preferred inference-time settlement reference. Full freely downloadable historical BRTI should not be assumed. Historical Kalshi settlement values provide exact outcome labels, while exchange history supplies historical path features.

## Data truth hierarchy

For this bootstrap subsystem:

1. **Outcome truth:** Kalshi historical market `result` and `settlement_value_dollars`.
2. **Live settlement reference:** Kalshi CF Benchmarks BRTI feed already captured by the production collector.
3. **Historical Kalshi probability/flow:** official historical trades and 1-minute market candlesticks.
4. **Historical BTC path:** official Binance public BTCUSDT spot 1-second klines and/or trades/aggregate trades.
5. **Supplemental futures information:** Binance BTCUSDT USD-M data only after ablation demonstrates incremental untouched-OOS value.
6. **Optional Coinbase reference:** admitted only if a reproducible historical path with adequate timestamp granularity is available and adds untouched-OOS value.
7. **Third-party KXBTC15M archives:** quarantined by default. They may enter an experiment only after provenance, timestamp semantics, completeness, duplicate/leakage risk, and settlement labeling are audited. The first production bootstrap model must not depend on an unaudited third-party dataset.
8. **First-party collector history:** gradually becomes the preferred training source as coverage grows because it contains exact wire-level Kalshi order books, BRTI, trades, lifecycle, and source/receive timestamps.

## Storage separation

Bootstrap data must never be written under the canonical Phase 1 `data/raw` hierarchy.

Use distinct roots conceptually equivalent to:

```text
bootstrap/
  raw/
    kalshi/
    binance/
  manifests/
  derived/
  models/
  reports/
  predictions/
```

The actual root is configurable, with a safe default under a separate ignored directory such as `data/bootstrap/`.

Every raw download manifest records at minimum:

- source name and endpoint/archive path;
- retrieval timestamp;
- requested date/ticker range;
- HTTP/archive metadata when available;
- SHA-256 of downloaded content;
- parser/schema version;
- normalized timestamp unit;
- row count;
- any detected gap/duplicate statistics.

Derived tables and models must record the hashes of the raw inputs from which they were produced.

## Historical market universe

Discover KXBTC15M markets programmatically rather than hardcoding a start date.

For each historical market retain:

- market ticker;
- event ticker;
- open/close/settlement timestamps;
- strike fields and rules fields;
- result;
- settlement value;
- liquidity, volume and open interest metadata;
- raw market JSON hash.

Markets with ambiguous rules, missing labels, provisional/unresolved state, inconsistent timestamps, or unusable strike definitions are excluded and reported rather than silently coerced.

The training label is the official market result. The predictor's human-facing ABOVE/BELOW mapping must be derived from the actual KXBTC15M contract/rule semantics and tested against historical `result` plus `settlement_value_dollars`; do not hardcode an inequality without that verification.

## Point-in-time training observations

Each 15-minute market produces multiple prediction observations, but all observations belonging to a market share one split group.

Primary checkpoint grid:

```text
14m, 13m, 12m, 11m, 10m, 9m, 8m, 7m, 6m, 5m,
4m, 3m, 2m, 1m, 45s, 30s, 20s, 10s remaining
```

A checkpoint is included only when every required feature can be reconstructed strictly from information observable by that checkpoint.

Because official historical Kalshi bid/ask candles are 1-minute, quote-dependent historical features must respect candle availability. The training pipeline must not use a candle's eventual high/low/close before its period has completed. Sub-minute checkpoints may use timestamped trades and external BTC path data, but any quote-derived feature must carry explicit staleness and use only the last quote observation legitimately available by that time.

The live predictor may update every second because live Kalshi order-book and BRTI observations are available at that frequency, but it must preserve the historical feature semantics used by each learned component. Live-only features that have no historical counterpart cannot silently enter a trained model.

## Feature families

### Contract/time geometry

- seconds remaining;
- elapsed fraction of market window;
- strike value;
- current settlement-reference distance to strike in dollars;
- distance in basis points;
- distance divided by realized volatility at multiple horizons;
- interaction of normalized distance with time remaining.

### BTC spot path

From official high-frequency BTCUSDT history/live reference feeds:

- returns over 5s, 10s, 15s, 30s, 60s, 2m, 5m, 10m;
- realized volatility over 15s, 30s, 60s, 3m, 5m, 15m;
- high/low excursion from strike and from window open;
- signed distance velocity and acceleration;
- short-horizon range and range expansion;
- trade count and quote/base volume;
- taker-buy versus taker-sell imbalance when source data supports it;
- large-trade intensity using thresholds defined from past-only rolling distributions.

### Optional futures features

Only if ablation validates them:

- futures-versus-spot basis;
- futures return lead/lag;
- taker flow imbalance;
- mark/index or premium features where reproducibly available historically and live.

No futures feature enters the final model merely because it sounds predictive.

### Kalshi market probability and microstructure

Point-in-time historical reconstruction from trades/candles and live reconstruction from the collector:

- YES bid and ask;
- executable midpoint / bounded market prior;
- bid-ask spread;
- last traded YES probability;
- traded-probability returns over multiple horizons;
- trade count and volume windows;
- YES-versus-NO taker pressure;
- probability acceleration;
- current/open interest where observable;
- quote/trade staleness;
- live-only order-book depth/imbalance as diagnostic or structural inputs until sufficient first-party history exists to train them safely.

### Cross-market discrepancy

- structural probability minus Kalshi prior;
- BTC normalized distance versus Kalshi logit probability;
- Kalshi probability reaction lag following BTC moves;
- cross-venue BTC divergence where multiple vetted sources exist.

## Component model A: Structural settlement model

This component estimates resolution probability from the underlying path without trusting Kalshi's market probability.

Before the final minute, use an empirical/parametric short-horizon path model whose volatility and drift state are estimated exclusively from past observations. Candidate dynamics may include a zero/weak-drift diffusion with realized-volatility scaling and an empirical residual bootstrap. The exact candidate is selected on chronological validation, not by intuition.

The output is:

```text
p_structural = P(KXBTC15M resolves YES | observable BTC/BRTI path state)
```

### Final-minute exact-average mode

When the KXBTC15M settlement mechanism is within its final 60-second observation window, the structural engine switches modes.

Let the strike/rule threshold be `K`, let `n` official BRTI observations already be observed in the settlement window, and let their sum be `S_n`. For 60 total observations, the required mean of the remaining observations is:

```text
required_remaining_mean = (60 * K - S_n) / (60 - n)
```

The model simulates/bootstraps only the remaining BRTI path conditional on the current state and converts the distribution of the final 60-observation mean into resolution probability. If the exact rule uses a strict versus non-strict comparison, that rule is taken from the verified contract semantics rather than assumed.

If final-minute BRTI observations are missing, duplicated, stale, or timestamp-ambiguous, final-minute confidence is downgraded or prediction fails closed.

## Component model B: Historical outcome model

Train a tabular probabilistic classifier on the point-in-time feature matrix to predict the official Kalshi result.

Candidate learners should include at least:

- regularized logistic regression as the low-variance reference;
- a gradient-boosted tree implementation suitable for nonlinear interactions.

Additional learners are admitted only if they improve untouched chronological probability metrics after calibration. The implementation plan may select LightGBM, XGBoost, CatBoost, or a scikit-learn histogram gradient booster based on reproducibility, Windows/Linux packaging, training speed, and validated performance.

Hyperparameter selection is performed only inside chronological training/validation folds. The final untouched lockbox is never used for model or hyperparameter selection.

## Component model C: Kalshi residual/calibration model

The primary learned-edge formulation predicts a correction to the contemporaneous Kalshi probability rather than blindly predicting direction from scratch.

Conceptually:

```text
logit(p_fair) = logit(p_kalshi) + residual(features)
```

where `p_kalshi` is derived from contemporaneous executable YES bid/ask information with explicit spread/staleness handling.

This component learns historical situations where market probabilities were systematically miscalibrated conditional on observable BTC path, volatility, time remaining and market-flow features.

The residual model must be compared directly against the uncorrected Kalshi prior. If it does not improve untouched chronological Brier/log loss/calibration, its ensemble weight is zero.

## Ensemble and calibration

The live final probability combines only components that earn positive untouched-OOS value.

Candidate inputs to the stacker are:

- contemporaneous Kalshi market prior;
- structural probability;
- historical outcome-model probability;
- residual-corrected market probability.

Stacking weights are learned from out-of-fold chronological predictions, never manually tuned on the final test set.

Calibration is fit on a chronologically later calibration slice using Platt/logistic calibration or isotonic regression, with method selection based on calibration-set Brier/log loss and stability. Per-time-remaining calibration may be used only if sample size is sufficient and it improves later-period validation.

The system records both raw and calibrated probabilities.

## Validation architecture

### Split unit

The market ticker is the indivisible grouping unit. All observations from one 15-minute market reside in exactly one fold/split.

### Chronological structure

Use expanding-window or rolling walk-forward evaluation. Example structure:

```text
train past markets -> validate next block
expand training set -> validate following block
...
final development cutoff -> untouched lockbox
```

The exact calendar boundaries are chosen after market discovery so each fold contains enough independent market windows and multiple volatility regimes.

No random row-level train/test split is permitted.

### Embargo and dependence

Adjacent 15-minute markets share BTC regime information. Fold construction must test an embargo around evaluation blocks and report sensitivity to daily/weekly grouping so performance is not overstated by serial dependence.

### Required prediction metrics

At minimum:

- Brier score;
- log loss;
- calibration curve/reliability table;
- expected calibration error or an equivalent explicitly defined calibration statistic;
- directional accuracy as a secondary metric;
- probability sharpness/distribution;
- bootstrap confidence intervals clustered by market/day where appropriate.

### Mandatory benchmarks

Every candidate must be compared with:

1. contemporaneous Kalshi market prior;
2. structural-only model;
3. simple regularized logistic reference;
4. 50% naive reference for context only.

The market prior is the principal benchmark.

### Ablation

Report out-of-sample metric changes when removing or adding:

- external BTC features;
- futures features;
- Kalshi trade-flow features;
- structural probability;
- residual model;
- optional third-party data.

A feature family that does not improve later-period validation is removed from the production bootstrap model.

## Leakage audit

The pipeline must automatically test for common leakage classes:

- using settlement/result-derived fields as features;
- reading candle close/high/low before the candle completed;
- using future trades in rolling windows;
- using market metadata updated after the checkpoint;
- normalizing with full-dataset statistics;
- defining large-trade/volatility thresholds using future data;
- mixing observations from the same market across splits;
- using today's first-party collector history to train a model evaluated on earlier timestamps without a valid chronological cut.

Every production model report includes an explicit leakage-audit result.

## Live inference architecture

The bootstrap live predictor runs as a separate process from `kalshi-edge collect`.

Conceptual commands:

```text
kalshi-edge bootstrap-backfill
kalshi-edge bootstrap-train
kalshi-edge predict-live
kalshi-edge bootstrap-evaluate
```

Final command names may differ, but collector commands and semantics remain backward compatible.

The live predictor consumes read-only live information from one of two safe paths:

1. the collector's finalized/current local observations through a dedicated read-only tail/reader interface that never mutates raw segments; or
2. its own separate authenticated/public read-only subscriptions if coupling to the collector would risk collector reliability.

The implementation plan must choose the isolation strategy that cannot block collector fsync/write loops. Collector reliability has priority over predictor latency.

### Live prediction record

Each emitted prediction contains at minimum:

- prediction timestamp;
- active market ticker;
- model artifact ID/hash;
- market strike/rule threshold;
- seconds remaining;
- live BRTI/current reference;
- Kalshi YES bid/ask and market prior;
- `p_structural`;
- `p_outcome_model` if admitted;
- `p_residual_corrected` if admitted;
- final raw probability;
- calibrated `p_yes` / `p_no`;
- predicted side;
- confidence/reliability label derived from calibration/coverage rules, not arbitrary percentages;
- feed staleness and quality flags;
- feature-schema version;
- no-trade/read-only marker.

Predictions are written to an append-only bootstrap prediction log separate from canonical collector data.

## Confidence semantics

Confidence is not the same thing as distance from 50%.

A confidence/reliability label considers:

- calibrated probability extremity;
- historical sample support for the current time/distance/volatility region;
- component agreement;
- calibration error in the relevant validation bucket;
- data freshness;
- whether current features lie out of training distribution.

If the live state is materially out of distribution or a required feed is stale, the engine emits low reliability or `NO_PREDICTION` even if a raw model probability is extreme.

## Economic comparison without automatic trading

The predictor remains read only, but its report may calculate hypothetical executable edge for evaluation:

```text
edge_yes = p_yes - executable_yes_ask
edge_no  = p_no  - executable_no_ask
```

Any EV display must use verified current fee rules and explicit spread/slippage assumptions. It is diagnostic only and does not authorize an order.

Probability quality and calibration are evaluated separately from hypothetical trading economics.

## Model registry and reproducibility

Each trained model artifact gets an immutable ID and manifest containing:

- Git commit SHA;
- training code/config hash;
- feature-schema version;
- raw/derived dataset hashes;
- training/validation/lockbox time ranges;
- included/excluded market counts;
- learner/hyperparameters;
- calibration method;
- benchmark metrics;
- leakage-audit results;
- environment/package versions;
- artifact SHA-256.

The live predictor refuses to load an artifact missing this manifest or with mismatched feature schema.

## Transition from bootstrap data to collector-native data

The bootstrap system is not replaced simply because the Phase 1 collector reaches 24 hours.

The transition is evidence-based:

1. keep the bootstrap model frozen as a benchmark;
2. accumulate first-party collector data;
3. build a collector-native feature matrix using exact BRTI, order-book, trades and lifecycle;
4. train candidate collector-native models under the same chronological/leakage governance;
5. shadow both systems on future unseen markets;
6. switch the production read-only predictor only when the collector-native system demonstrates superior probability quality/calibration and operational reliability on untouched future data;
7. retain the prior model for rollback and comparison.

As first-party history grows, proxy/third-party features may be downweighted or removed if ablation shows the exact collector features dominate them.

## Operational safety

The bootstrap process must not materially impair the collector.

- Historical downloads/training use separate processes and directories.
- Bulk training may be CPU/memory intensive and should not starve the collector process.
- Disk-space checks run before bulk backfill; large source archives are bounded by configurable date/source scope.
- Canonical collector files remain immutable.
- Predictor crashes do not stop the collector.
- Collector crashes do not cause predictor code to rewrite collector data.
- All API interactions in this subsystem are read only.

## Initial implementation sequence

Implementation planning should decompose this architecture into independently reviewable subsystems:

1. bootstrap source/provenance framework and Kalshi historical market universe;
2. Kalshi historical trades/candlesticks backfill;
3. Binance high-frequency historical path backfill and timestamp normalization;
4. point-in-time feature/label builder with leakage tests;
5. structural settlement engine including final-minute mode;
6. chronological evaluation framework and market-prior benchmarks;
7. learned outcome/residual candidates plus calibration/stacking;
8. immutable model registry/reporting;
9. isolated read-only live predictor and prediction log;
10. operational backfill/train/live runbook.

Each subsystem must be testable before the next consumes it.

## Acceptance criteria for the bootstrap predictor

The predictor is eligible for immediate read-only use only when all of the following are evidenced:

- historical KXBTC15M market universe and exact labels are reproducibly backfilled;
- raw source hashes/provenance are recorded;
- point-in-time feature builder passes leakage tests;
- at least one chronological walk-forward evaluation is complete;
- final untouched lockbox was not used for feature/hyperparameter/calibration selection;
- probability metrics are reported against the contemporaneous Kalshi market prior;
- calibration is measured and the live confidence policy is defined from validation evidence;
- model artifact and feature schema are immutable/versioned;
- live predictor receives current KXBTC15M, Kalshi quotes/trades and BRTI without mutating or blocking the collector;
- stale/out-of-distribution inputs can cause `NO_PREDICTION`;
- no trading/order-placement capability is present;
- collector continues operating independently.

If the learned ensemble fails to beat a simpler component on untouched probability metrics, the production bootstrap predictor uses the best validated simpler component rather than forcing the more complex ensemble.

## Success definition

Success is not "the AI outputs a number." Success is a reproducible, point-in-time, chronologically validated probability engine that uses the best historical data currently available, honestly benchmarks itself against Kalshi, can operate immediately in read-only mode, and is designed to be superseded only when the growing first-party collector dataset demonstrates a better model on future unseen markets.
