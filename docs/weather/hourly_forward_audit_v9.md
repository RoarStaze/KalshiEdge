# WeatherEdge V9 — prospective, settlement-verified hourly accuracy

## Objective
Measure hourly Kalshi Weather Index forecast skill at distinct real-time horizons. Existing V8 forecasting and all price/order paths remain unchanged. No backfilled/synthetic forecasts may be counted as prospective.

## Alternatives
- Chosen: new immutable checkpoint and exact-ticker settlement tables, written in the existing weather worker every index cycle and reconciled against official Kalshi responses. This supports per-city, per-lead and per-model comparisons while preserving V1's single-event journal.
- Not chosen: use historical replay without original local receipt timestamps (hindsight leakage); infer binary results from an airport thermometer (wrong underlying); aggregate all successive predictions for one hour into an artificially independent sample.

## Frozen checkpoints
- One first-stored freeze per city/event/nominal lead bucket (60, 45, 30, 15, 5 minutes). Accept actual lead within ±1.5 minutes, store exact asof/actual lead.
- Only source-fresh READY_RESEARCH forecasts with an exchange-validated listed hourly event and selected one nearest-50% threshold; market metadata and observation receipts must be no later than asof; asof must precede target and the market's listed close.
- Freeze exact model_kind, calibration/config identity, predicted_index_f, sigma_f, selected probability/ticker/threshold, asof, original source timestamps and source hashes. Store SHA256 of canonical JSON; INSERT OR IGNORE, never overwrite.
- Keep a separate new table from legacy snapshots. No future outcomes, and no modifications to the forecasting or order pipeline.

## Settlement matching
- Process eligible closed targets 6 minutes to 7 days after target with public read-only Kalshi endpoints. Match exact city, event, ticker, threshold, and exact target-hour official index event_ms (normal status). Reject missing/late/inconsistent index values, cursor-truncated markets lists, unsettled/ambiguous results, mismatch between binary result and canonical index.
- Persist original source JSON bytes, their SHA256 digests, observation timestamp and settlement timestamp with an immutable first-insert per (city,event,ticker).
- Bad source data never creates a positive outcome. A missing result remains pending; no retroactive substitution of different market tickers.

## Reporting
- Read-only, fail-closed audit replays hashes and reconciles raw Kalshi responses for every scored row.
- Group by (city, lead bucket, model_kind); show unique verified target hours and pending rows. For each cohort with at least 10 verified distinct target hours, report MAE (°F), Brier, log loss, empirical nominal 90% interval coverage. Scores remain null below 10; show count and collection status. Do not pool correlated per-hour forecasts or claim statistical significance from small samples.
- Provide a concise calibration table via existing `/api/weather/hourly/calibration` and hourly WeatherEdge page; no extra user startup command.
- No model promotion or feedback from held-out scores; V8 and V7 model identities stay distinguishable, no automatic weather trading.

## Non-goals and evidence limitations
- Does not manufacture settlement labels; exact Kalshi Weather Index values are publisher reports, not independently confirmed sensor readings.
- SHA256 protects against accidental mutation of stored payloads but does not establish independent trust of a machine-owner's database.
- Tests with fixed fake responses establish logic only, not measured accuracy or profitable execution.
