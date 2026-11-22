# WeatherEdge hourly-only conversion — 2026-11-22

## Decision
Switch the Weather workspace **exclusively** to hourly directional Kalshi Weather Index events; do not reuse daily high station forecasts, settlement labels, or calibration as if hourly. Preserve historical daily SQLite tables untouched but remove daily forecasts and daily settlement polling from the active Weather supervisor.

## Verified market contract
- Active HS index series (Kalshi): `KXTEMPCHIHS` Chicago Metro (`chicago`), `KXTEMPNYCHS` New York (`nyc`), `KXTEMPLAXHS` Coastal LA (`la-coastal`), `KXTEMPMIAH` Miami (`miami`).
- Canonical numeric underlying is Kalshi Weather Index, not a single airport or daily max. A point's source is Kalshi's `GET /trade-api/v2/live_data/weather/{city}` in °F, 0.01 precision, sampled minute by minute. The event time is `t` (Unix ms); receipt timestamps must be logged locally; backfilled points must never retroactively create past predictions.
- Contract ticker uses an ET target hour, but only actual market metadata/rules determine eligible strikes, operators and settlement. For at contracts the most recent canonical index minute no more than 60 minutes before the settlement time wins; first publication governs, after five-minute publication deadline. See official `CHIMETROWINDEX.pdf`.
- Contract outcomes are binary directional thresholds, not mutually exclusive daily temperature ranges. Do not claim current hourly market is settled by old Weather Company daily publisher or Chicago Midway. Old station ET/METAR/NWP feeds may remain as archived input records, but are NOT hourly index settlement truth.
- Prices and execution: no live orders, no EV or P&L without verified accuracy and traded bid/ask.

## Implementation
1. New typed hourly index collector fetches actual Kalshi minute points and event threshold ladders, with strict unit/time/status validation and explicit failure statuses; stores event-time + **actual receipt-time** in `data/weather/weatheredge.sqlite3` distinct new tables.
2. Conservative, clearly uncalibrated, real-time one-hour index forecaster: last published index reading + clipped recent trend; lead-dependent assumed sigma. Model only yields threshold YES probabilities if current event metadata and source match. Never pretend Gaussian 90% interval is proven coverage.
3. Frozen prospective event snapshots for a precommitted closest-to-50% directional threshold. Reconcile only actual settled Kalshi results, with canonical Index value agreement for exact event time and no age >60m; score Brier/log loss per unique event, not per strike. No historical reconstruction from after-the-fact readings.
4. Weather page shows ONLY hourly events, four index cities, target ET hour and UTC, canonical/latest reading, current readout, readiness, threshold probability bars, and real eligible sample/score counts; no daily-high forecast or station-specific training controls.
5. Replace the WeatherSupervisor's active daily loops with hourly index, hourly markets and hourly settlement verification under existing `kalshiedge start`; retain BTC and Gold isolation and read-only APIs. Daily modules remain solely for legacy data compatibility.

## Tests before code
- [ ] Reject non-Fahrenheit/missing/future/backfilled/stale index minutes; deduplicate and retain first witnessed value
- [ ] Convert ET target-hour event tickers across day boundary and DST, avoid lookahead
- [ ] Validate strict market series, threshold, source, open state; verify Gaussian inclusive threshold p
- [ ] Freeze one prediction per city/event before target; no cherry-picked post-close snapshots
- [ ] Settle only with Kalshi result consistent with canonical 0.01°F index at target; don't imply independent certification
- [ ] Weather UI/route tests: hourly ONLY, no daily high, no synthetic P&L/orders, no extra commands
- [ ] CI all Python, optional local smoke; squash into `main` only if green

## Sources
- https://docs.kalshi.com/api-reference/live-data/get-weather-index
- https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations
- https://assets.kalshi.com/contract_terms/CHIMETROWINDEX.pdf
- https://kalshi.com/category/climate/hourly-temperature
