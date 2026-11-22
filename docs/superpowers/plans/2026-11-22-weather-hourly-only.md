# WeatherEdge hourly-only conversion — 2026-11-22

## Goal
Make the Weather option in KalshiEdge forecast **only Kalshi hourly temperature index markets**, not daily highs. Keep the existing historical daily SQLite rows and Python modules untouched for audit, but remove daily collection, settlement, training and calibration from active startup and browser navigation.

## Verified contract details
- Kalshi's official Get Weather Index API: GET /trade-api/v2/live_data/weather/{city}, timeseries with minute event time `t` (Unix ms), index `v` in °F to 0.01, status, contributors and config_version. Missing/quorum-failed minutes are omitted.
- Get Weather Index Calibrations: GET /live_data/weather/{city}/calibrations; station weights/offsets and effective configuration version.
- New hourly HS-style markets are **city index**, not a single station's daily max. Four active city IDs: chicago, nyc, miami, la-coastal. Market contract names define settlement time and comparison operator. Never assume daily KXHIGH* ticker rules or airport equivalence.
- Canonical settlement value can differ from an airport METAR; do not use daily weather.com climate high as a label.

## Implementation design
1. New `src/kalshi_edge/weather/hourly.py`: pinned city config; async public Index poll every 60s; validated schema/units/timestamps/first_seen_at; SQLite `weather_hourly_index` append-only first-receipt ledger, no fabricated missing points. Missing index => no estimate.
2. Forecast next top-of-hour **instant**, not hourly maximum. Use current index level and bounded short-window index trend, with explicitly assumed Gaussian spread that grows with lead time. Show one-degree binary threshold probabilities `P(index >= strike)` with monotonicity. No calibration claims or real orders.
3. Save frozen short-horizon forecasts in new `weather_hourly_forecasts` per city/target/pre-registered 5-minute snapshot, no lookahead. Score only when official canonical t exactly matches target with publication after forecasting. Label **INDEX_OBSERVED_NOT_EXCHANGE_SETTLED**; no precision/settlement certification or P&L.
4. Managed `HourlySupervisor` becomes the only weather worker launched by `kalshiedge start`; legacy WeatherSupervisor continues to exist for old unit tests but isn't launched. No METAR/HRRR/NBM daily collectors in active runtime.
5. New read-only `/api/weather/hourly` and `/api/weather/hourly/health` with `preview=True` never reading machine's DB. New Weather Forecast/Train/History/Data UI reflects only four index cities and hourly check horizon; show sample statistics with no daily data.
6. No orders, no credentials, no promises of accuracy. After real data, station-member HRRR/NBM blend and exchange market-specific independent label reconciliation are subsequent model milestones.

## Test-first workflow
- [ ] Red: Index schema, monotonic threshold probabilities, clock consistency, stale/unavailable, first receipt and immutable journal tests.
- [ ] Green: implement official Kalshi index reader, storage, safe baseline nowcast and frozen journal.
- [ ] Red: one-command runtime and read-only API/preview/UI tests; old daily hidden without deleting source.
- [ ] Green: hourly-only supervisor, REST handlers, responsive UI.
- [ ] Verify full CI and build, inspect diff for order-writing scope, squash to main.
