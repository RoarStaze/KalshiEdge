# WeatherEdge 15-minute airport outlook — build plan

**Scope choice:** Kalshi officially lists hourly weather markets, not an independently verified 15-minute weather settlement series. Build a read-only 15-minute-ahead airport observation research forecast, which can later serve verified hourly-market trading. Do not treat it as hourly index settlement odds or fabricate a 15-minute weather contract.

## Modules and interfaces

1. `src/kalshi_edge/weather/short_horizon.py`:
   - `read_quarter_hour_forecast(db_path, station, asof)`: compute 15-minute-ahead airport temperature research forecast using ONLY source rows with receipt <= asof.
   - Exact target `asof + 15 min`; age-gated two or more METAR observations with a 10–45m trend, + complete same-cycle NOAA HRRR/NBM 2m-temperature hourly guidance that straddles the target; never mix cycles.
   - Continuous normal temperature proxy with assumed uncertainty; distinct probabilities of change >=+0.5°F, <=-0.5°F or nearly flat. Neither a calibrated model nor a Kalshi contract.
   - `read_quarter_hour_forecasts(root, preview)`: fixed-path, read-only endpoint.
2. `src/kalshi_edge/weather/short_horizon_journal.py`: append-only first research forecast per station per five-minute window; hash-protected, one-command supervisor-owned. No retroactive forecasts or synthetic labels; observation-only forward score measured separately from Kalshi settlement.
3. `src/kalshi_edge/weather/runtime.py`: adds one failure-isolated five-minute poll alongside existing sources, writes research heartbeat. No order clients, no new binary dependencies.
4. `src/kalshi_edge/weather/research_ui.py`: `/weather/15m` station picker, live prediction, observed vs NWP trend and source timestamps, data readiness, explicit assumption/settlement boundaries. Existing pages unchanged.
5. `src/kalshi_edge/bootstrap/final_history.py` and `preview_site.py`: `GET /api/weather/15m` read-only; preview reports no real observations and never reads disk.
6. Tests in `tests/weather/test_short_horizon.py`: as-of receipt exclusion, stale/no history, no cycle mixing, fixed probability mass, journal idempotency, no fabricated 15m tradability, route/preview parity, supervisor lifecycle and degradation. Validate via GitHub CI before merging to main.

**Data limitations:** Airport METAR may be issued only at intervals of 5–60 minutes; if the latest timestamp is older than 20 minutes, show STALE_METAR rather than invent a 15-minute forecast. NOAA HRRR/NBM hourly temperature values are interpolated only as slopes over the immediate hour, not transformed into Kalshi Weather Index values. No model is independently calibrated at this horizon.

**No orders:** API, UI and journal remain PAPER_ONLY. No Kalshi weather live order code. Later research must verify contract-specific hourly index methodology, publisher and station weights, before translating forecast into market fair value.
