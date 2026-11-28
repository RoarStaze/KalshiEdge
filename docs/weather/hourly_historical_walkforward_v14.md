# WeatherEdge V14 — 90-day archived Index walk-forward research

**Mode:** offline historical reconstruction; research-only. **NOT** a proof of live-pipeline superiority, settlement accuracy, calibration, or profitability.

## Source and scope

- Endpoint: Kalshi's public `GET /trade-api/v2/live_data/weather/{city}` with UTC-millisecond `from`/`to` windows. It may not return every older minute; absence is measured, not filled in. Source documentation: https://www.orthogonal.com/api/kalshi/get-weather-index and https://www.pipeworx.io/docs/reference/kalshi/kalshi_weather_index/.
- Data are original returned official **Index values** (as archived at retrieval), not interpolated individual-airport temperatures, ERA5 reanalysis, or future HRRR guidance.
- At most 90 days, fetched one UTC day per request per city. Each response is size-limited and SHA-256 documented. Only normal/degraded values, finite 0.01°F readings, contributor count >0, source-audited configuration identity, and exact hour labels enter a case. Invalid, duplicate, and cross-config inputs fail closed or are excluded.
- Historical responses can omit `config_version` (including empty windows), and their response-level version may not identify the configuration of every old minute. For populated windows the importer requests Kalshi's independent `GET /live_data/weather/{city}/calibrations` timeline, assigns the `config_version` effective at each event millisecond, and rejects disagreeing per-minute tags, unavailable histories and calibrations published after the event. Original response hashes and the timeline hash are retained in the research report. No guessed version or interpolated Index value is allowed.
- **Crucial epistemic limit:** the archived API is not a first-publication receipt ledger. The experiment *assumes* a point is available at event time +10 minutes. It does not claim that this was the actual published time. No archived observations, assumptions or derived forecasts enter the original live SQLite evidence tables.
- Kalshi's Chicago index contract terms distinguish the original canonical event minute from its publication time, and specify a five-minute post-settlement determination delay; this does not make archive retrieval timestamp an as-of receipt. https://assets.kalshi.com/contract_terms/CHIMETROWINDEX.pdf

## Model comparison

- For each city and lead (5, 15, 30, 45, 60 minutes), rebuild the existing `_cases_from_points` inputs, using only source points assumed available before each historical decision timestamp.
- Reset training when the index configuration changes or canonical target hours are discontinuous.
- After 96 prior source-valid hours, evaluate non-overlapping 24-hour future blocks. At the first decision in each block, exclude labels whose *assumed* release time is later than that decision, and use at most the preceding eight days.
- V12 baseline: original `choose_index_nowcast` + `nowcast_prediction` observation-component path. This is **not** a reconstruction of full historical HRRR/NBM ensemble or settlement correction.
- V13 challenger: existing `fit_hybrid` on the **same earlier cases**, existing `predict_hybrid` on untouched blocks. No outcomes from later blocks enter fitting.
- Report paired MAE (°F), Gaussian CRPS, nominal 90%-interval empirical coverage, fold dates, sample counts and CRPS wins. No Brier/log loss or claimed Kalshi betting accuracy without verified originally frozen strike-and-outcome pairs.
- **No promotion:** `eligible_for_live_promotion=false`, `calibration_certified=false`, `real_orders_enabled=false` for all paths. The existing live V13 frozen-forward gate remains entirely separate.

## Run on the user's Windows computer

From the repository root after pulling main:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.weather.hourly_historical_backtest --days 90 --output data\weather\historical-backtest-v14.json
```

For a fast source-availability check for one city and forecast horizon:

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.weather.hourly_historical_backtest --days 14 --city chicago --lead 30 --output data\weather\historical-chicago-30.json
```

`--end-utc 2026-11-27T00:00:00Z` freezes the end date for reproducibility. No orders are placed; the normal app and paper History remain untouched.

**Return code:** 0 if at least one historical city/lead cohort was scored, 2 if no cohorts could be scored (including inaccessible or too-short archives). All source gaps and reasons appear in JSON; `null` metric means not measured, not zero accuracy.

## Interpretation and next research

The historical archive may simply not have 90 days of original Index observations. In that case the program reports `ARCHIVE_SOURCE_UNAVAILABLE` or `NO_ARCHIVED_INDEX_HISTORY` and does **not** synthesize observations or claim an accuracy gain.

If archived replay suggests a challenger, collect original live frozen predictions and the exact verified Kalshi settled contract outcomes, then run the original V13 source-audited promotion gate independently. A historical reconstructed V12 observation-only baseline may be materially weaker or stronger than the full production model.
