# GoldPredict AI V1 — prospective evidence, dataset readiness and XGBoost skeleton

Date: 2026-11-17. Scope: **KXGOLD15M** only. No BTC files or live trading behavior change.

## Source-of-truth limitations

Existing `data/gold/market_inventories/` market results are *observed Kalshi API values*.
They are **not** independently reconstructed Pyth oracle settlements.
Existing `data/gold/candle_probes/` snapshots are retrospective YES-price candles; the
`end_period_ts` of a candle is not evidence that a live client had received it by
the proposed decision timestamp. The `close_time - 480 seconds` checkpoint
is a candidate until the series' contract-time semantics, including EST/EDT
conflicts, are conclusively resolved. Historical gold training is BLOCKED.

The exact source symbol is `Metal.Index.1OZGOLD/USD`. Gold quoted prices or
other XAU/USD feeds are not interchangeable with that Pyth settlement index.

Primary source:
- https://docs.kalshi.com/api-reference/market/get-markets
- https://docs.kalshi.com/api-reference/market/get-market
- https://docs.kalshi.com/getting_started/historical_data
- https://github.com/RoarStaze/KalshiEdge/blob/main/docs/GOLD_CONTRACT_RULE_INSPECTION_2026-11-16.md
- https://github.com/RoarStaze/KalshiEdge/blob/main/docs/GOLD_CANDLE_CHECKPOINT_PROBE_2026-11-17.md

## Read-only prospective capture

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --capture-quotes --output-root data/gold
```

Captures bounded anonymous `GET /series/KXGOLD15M` and
`GET /markets?series_ticker=KXGOLD15M&status=open` payloads. Records both local
request start and response receipt nanosecond timestamps, HTTP Date if present,
full unaltered market objects, canonical source ID and pagination status in a
new SHA-256-named immutable file under `data/gold/prospective_quotes/`.
Rejects malformed quotes, crossed bid/ask, wrong series, non-open markets,
repeating cursors, invalid close times or clock reversal. Each invocation
captures **one bounded snapshot**, not a continuous stream.

A receipt before a *candidate* checkpoint is only a prospective sample. It
cannot prove exchange publication time, exact executable liquidity, a Pyth
settlement observation, or a verified official checkpoint. The Kalshi API
warns that market-listing responses can be cached/delayed. Any pages omitted
due to a cap are explicitly reported. Local system clocks must be monitored.

## Immutable dataset readiness audit

Use a real SHA-named inventory artifact generated earlier, plus optional quote
and candle artifacts (replace the example paths with your actual filenames):

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --audit-dataset "data\gold\market_inventories\<your-inventory>.json" --quote-artifact "data\gold\prospective_quotes\<your-quotes>.json" --output-root data/gold
```

Optional: add `--candle-artifact "<your-probe>.json"` for historical candle
inventory diagnostics. This *does not* turn backfilled candles into live inputs.

The new `data/gold/dataset_audits/` file contains source SHA-256 hashes,
gold-only candidate ticker counts, contradictory observed result counts,
retrospective candle counts and explicit blockers. It intentionally has
`training_rows=[]`, `eligible_training_rows=0`, `dataset_ready=false`.
Any source that attempts to set its own `ready_for_training=true` fails
validation. A file checksum verifies consistency; it does **not** establish
source authenticity or a correct settlement interpretation.

## Isolated XGBoost research utilities

`gold/xgboost_baseline.py` exposes `GoldModelRow`,
`chronological_gold_split` and `fit_research_baseline`. The split separates
UTC market dates and excludes training labels not finalized before the first
holdout prediction. It rejects duplicate markets, mismatched series,
feature receipts after decisions, nonbinary labels and invalid quote features.
The non-deployable XGBoost harness is tested **only with synthetic rows**.
Outputs are explicitly `research_only=true`, `deployable=false` and are
neither persisted as a live model nor promoted.

`train_from_gold_audit(...)` rejects current dataset-audit artifacts because
they have zero verified rows. There is **no** gold training CLI, fitted model,
validated gold probabilities, or real gold accuracy to report in this phase.

## Gates before an empirical gold training run

1. Capture licensed/exact-index Pyth oracle observations with auditable source
   identity, first-available timestamps and validated historical coverage.
2. Resolve contract-rule start/end observations, tie/rounding behavior, fallback
   policies, timezone ambiguities and the correct decision-time checkpoint.
3. Independently reconstruct settlements and compare against official finalized
   Kalshi outcomes; quarantine conflicts and retroactive changes.
4. Build per-market as-of features from prospective receipts with causal
   timestamps, synchronized clocks and duplicate/invalid receipt rejection.
5. Create immutable validated training rows with day-based chronological
   train/calibration/test partitions and a frozen unseen lockbox.
6. Run XGBoost in a research-only offline evaluation. Report true out-of-sample
   accuracy, Brier/log loss, coverage and execution-aware EV only if the data
   proves it; compare to a calibrated simple baseline before considering
   promotion.

Gold UI, trading, automated orders, live predictions, and training remain
disabled until a future independently verified promotion gate explicitly
authorizes them. No predictive performance or profits are claimed.

## Tests

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/gold
& .\.venv\Scripts\python.exe -m pytest -q
```
