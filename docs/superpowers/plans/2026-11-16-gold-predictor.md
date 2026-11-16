# KXGOLD15M evidence-first implementation plan (2026-11-16)

> Required workflow: Superpowers planning, test-driven development, Dyslex.ai identifier-safe reports, independent GitHub review and CI. This plan is not trading authorization.

## Goal and nonnegotiable settings

Forecast the probability of the **actual Kalshi KXGOLD15M contract settling YES**, at a single immutable **480-second** point-in-time checkpoint, using a **0.69 directional probability qualification gate**, and separately evaluate executable trading value. Log below-gate opportunities. Preserve existing BTC model, training, collector, live orders and account state.

**Verified series identity:** `KXGOLD15M`; source `Metal.Index.1OZGOLD/USD`, Pyth feed id 3712. Do not substitute `Metal.XAU/USD`, `Metal.Index.GOLD/USD`, futures settlement, or a free gold chart.

**Gate semantics:** `p_win = max(p_yes,1-p_yes)`. Flag `QUALIFIED` when `p_win > 0.69` to maintain the existing BTC gate's at-or-below skip policy; this is not permission to trade and does not prove positive EV. The gold model must be calibrated on its own genuine data before any percentage is displayed as a model probability.

## Repo inspection (verified in current main)

| Capability | Real implementation | Gold handling |
| --- | --- | --- |
| Discovery | `src/kalshi_edge/discovery.py` | Reuse public series and market GET patterns |
| Raw collector | `src/kalshi_edge/collector.py`, `protocol.py`, `storage.py` | Bitcoin-specific BRTI subscription: never reuse unchanged |
| Archive routing | `src/kalshi_edge/bootstrap/kalshi_history.py` | Reuse published historical-cutoff/REST routing concepts |
| Immutable artifact hashes | `src/kalshi_edge/bootstrap/provenance.py` | Reuse only with gold-isolated paths and no overwrite |
| Dataset/features | `src/kalshi_edge/bootstrap/dataset.py`, `features.py` | Bitcoin-specific Binance features: not gold inputs |
| Splits/scoring | `src/kalshi_edge/bootstrap/splits.py`, `metrics.py`, `calibration.py` | Reuse after validating grouping and gold metric semantics |
| Prediction | `src/kalshi_edge/bootstrap/live.py`, `final_forecast.py` | 480s/write-once/69% logic is a design reference only |
| History and orders | `bootstrap/final_history.py`, `trading/armed_autotrade.py`, `trading/fill_reconciliation.py` | BTC-only; do not modify/order gold without authorization |
| UI | `bootstrap/dashboard_ui.py`, `bootstrap/site_pages.py` | Gold section only after read-only forecast engine is validated |

## Source and cost comparison

| Source | Identity | Cost and use | Main limitation |
| --- | --- | --- | --- |
| Public Kalshi REST | Exact gold series, markets, rule texts, settled outcomes, recent quotes and candles | No extra paid market-data subscription assumed; free research baseline | Historic minute-by-minute depth and exchange queue cannot be reconstructed |
| Kalshi authenticated WS | Kalshi books and trade updates | Existing user API entitlement subject to account limits | Prospective only; timestamps and sequence must be validated |
| Kalshi Pyth-value WS | The exact `Metal.Index.1OZGOLD/USD` feed | **Advertised $1,000/month** with seven-day trial (verify again before purchase) | Historical access and licensed streaming terms unverified |
| Direct Pyth | Exact Pyth Gold index only with explicit indexed entitlement | Separately negotiated; unknown quote | Free/view-only and generic Pro access do not establish entitlement |
| Gold spot proxies | XAUUSD/other indices | Potentially low cost | Different calculation, timestamps and methodology; **not settlement-grade** |
| CME/COMEX GC futures | Delivery-month gold futures | Licensed vendor costs not yet quoted | Different instrument; roll/calendar/basis must be modeled |

**Lowest-cost setup:** Collect public Kalshi contract, quotes, trades and official outcomes. Evaluate a contract-only and market-price baseline. Prospective order books improve fill studies; no exact Pyth backtest or settlement-distance feature can be validated from free proxies.

**Enhanced setup:** With explicit approval of fees and rights, subscribe to an exact-index feed, collect original frames with receipt timestamps, and optionally license individual GC futures/depth and economic expectations; total operating cost is a separate line item. Do not buy subscriptions automatically.

Sources:
- Series: https://external-api.kalshi.com/trade-api/v2/series/KXGOLD15M
- Pyth feed: https://docs.kalshi.com/websockets/pyth-value
- Pyth pricing: https://app.pyth.com/products
- Historical cutoffs: https://docs.kalshi.com/getting_started/historical_data
- Orderbook: https://docs.kalshi.com/getting_started/orderbook_responses
- Fee rounding: https://docs.kalshi.com/getting_started/fee_rounding
- Commodity contract terms: https://assets.kalshi.com/contract_terms/COMMODITIES.pdf

## Tasks and stage gates

### Task 1. Rules and entitlement audit (initial increment)

**Files:** `src/kalshi_edge/gold/audit.py`, `src/kalshi_edge/gold/__main__.py`, `tests/gold/test_contract_audit.py`.

- [x] Check series ticker and **exact** Pyth URL, reject aliases.
- [x] Save a new SHA-256-identified immutable JSON object snapshot of public series, one market listing page, up to ten individual market details and archive cutoff with receipt times.
- [x] Force `ready_for_training=false`, `ready_for_live_trading=false`, and `settlement_rule_status=REQUIRES_MANUAL_VERIFICATION`. Record gaps and pagination; tests must use HTTP mocks and cause no account actions.
- [ ] Execute public API audit on the real Windows host and verify correct API availability.
- [ ] Confirm exact one-minute observation boundaries, target construction, settlement comparison, two-decimal rounding mode, equality Yes, missing-publication fallback, DST abbreviations, expiration versus close, series rule changes and official outcomes. Write timestamp/boundary regression tests. Do not create settlement labels before this.

### Task 2. Recorder and point-in-time dataset

**Proposed:** `gold/recorder.py`, `gold/dataset.py`, restart tests.

- [ ] Collect Kalshi current/archived markets, trades and candles with published per-type cutoffs; paginate to exhaustion and retain requests/raw responses, source and local receipt timestamps, correction versions and source identity.
- [ ] Record authenticated WS book snapshots/deltas and exact Pyth frames **only if entitled**; re-subscribe on sequence gaps, keep append-only segments, hash manifests and restart-safe capture.
- [ ] Emit availability audit with UTC date span, unique contracts, checkpoint coverage, feed age/gaps, rule versions and missingness. Multiple snapshots of a contract are not independent labels.

### Task 3. Settlement and live/replay features

**Proposed:** `gold/settlement.py`, `gold/features.py` and boundary/replay tests.

- [ ] Rule-versioned settlement oracle with exact comparison/rounding and missing Pyth observations; reconcile mismatches against official outcomes before eligibility.
- [ ] Compute target distance normalized by volatility, returns/jumps, time/session, stale/missing flags, Kalshi depth and spread, and event schedule. Later test GC month/roll, silver, USD, Treasury proxies only with synchronized rights/timestamps.
- [ ] For every feature require **receipt_timestamp <= forecast_timestamp**; label snapshot timestamp separately. Enforce partial-candle availability and deterministic live/replay equivalence.

### Task 4. Baselines and chronological testing

**Proposed:** `gold/baselines.py`, `gold/evaluation.py`, paired metrics tests.

- [ ] Hold entire contracts within chronological folds, reserve distinct training, calibration, policy selection and untouched final periods; predeclare multiple-testing budgets.
- [ ] Compare empirical base rate, Kalshi quoted implied probability, structural normalized-distance/volatility, regularized logistic, XGBoost and CatBoost only on genuinely accessible features; compare without price and against price.
- [ ] Report total unique contracts, missing rows, Brier, log loss, ECE/reliability, directional and qualified accuracy, 69% coverage, skip reasons and day/session-block uncertainty. Compare candidates on matched contracts at matched coverage.

### Task 5. Model and calibration

**Proposed:** `gold/training.py`, `gold/calibration.py`, `gold/model_bundle.py`, training tests.

- [ ] Fit and evaluate chronology-safe feature ablations, event-aware baselines and indicator-versus-return/volatility comparisons.
- [ ] Fit any structural+boosted blend on out-of-fold predictions only, then calibrate Platt/isotonic using separate post-fit data.
- [ ] Release hash-sealed model only upon predeclared unseen paired gain and no material regression in calibration/coverage; otherwise retain no gold model.
- [ ] Freeze prospective gold evaluation with a finite bound: **proposal** up to 60 calendar days and target 1000 resolvable unique contracts, or report inconclusive at deadline. Confirm power and tolerances before starting; not an indefinite collection.

### Task 6. Executable economics, audit trail, and UI

**Proposed:** `gold/decision.py`, `gold/final_history.py`, `gold/ui.py`, dedicated tests.

- [ ] One final per contract at exactly **480 seconds remaining**, no backdating and no overwrite. If not available, publish explicit skip reason. UI: `Estimated win probability`, 69% gate, data health, model and rule versions, no-order status.
- [ ] Compute YES and NO best attainable asks as complements of opposite bids, then net EV from direction, quantity, attainable price, **current fee schedule** and actual execution assumptions. Never assume full fills.
- [ ] Keep forecast/skip separate from order, ack, fill, position and settlement ledger; confirmed fills never display as only `Skipped`. No live gold orders without explicit authorization.
- [ ] Full suite, prospective comparison, rollback command and audited merge to main; keep existing BTC live behavior unchanged.

## Risk-focused test cases

- Tick crossing at two-decimal rounding boundary and exact equality.
- UTC versus incorrectly worded EST/EDT and source minute boundaries.
- Pyth source gap with a fresh but incorrect substitute index.
- Restart at 480s and duplicate final emission.
- A live confirmed NO fill after uncertain submission but a historical forecast SKIP.

## First-stage local commands

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --output-root data/gold --sample-markets 5
& .\.venv\Scripts\python.exe -m pytest -q tests/gold/test_contract_audit.py
```

Hardware diagnostics (run on the Windows host; GitHub cannot measure its current GPU/RAM state):

```powershell
Get-CimInstance Win32_Processor | Select-Object Name,NumberOfLogicalProcessors
Get-CimInstance Win32_PhysicalMemory | Measure-Object Capacity -Sum | Select-Object @{Name='RAM_GB';Expression={[math]::Round($_.Sum/1GB,1)}}
nvidia-smi --query-gpu=name,memory.total --format=csv
```

**Stage 1 is a collection/audit utility, not a trained or deployed gold model.** There are no measured gold forecasts, model comparisons, fills or returns as of this implementation increment. Known rule and access gaps are deliberately blockers.
