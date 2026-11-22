# WeatherEdge AI — hourly temperature markets only

## Start

The only command required after updating the repository is:

```powershell
kalshiedge start
```

The KalshiEdge launcher owns a read-only Weather worker. It polls Kalshi's official
Weather Index every **120 seconds** and reconciles previously forecast hourly
events every **5 minutes**, without interfering with BTC or Gold. No external
weather terminal, NOAA decoder install, or API key is required for this hourly
index mode. BTC and Gold are unchanged.

Navigate to **Markets → Weather → Forecast** in the KalshiEdge dashboard.

## Exact market underlying

The active hourly market series, as verified against Kalshi's November 22,
2026 hourly category and current contract terms, are:

| City index | Kalshi hourly series | Underlying |
| --- | --- | --- |
| Chicago Metro | `KXTEMPCHIHS` | Kalshi Chicago Metro Weather Index |
| New York City | `KXTEMPNYCHS` | Kalshi NYC Weather Index |
| Miami | `KXTEMPMIAH` | Kalshi Miami Weather Index |
| Coastal Los Angeles | `KXTEMPLAXHS` | Kalshi Coastal LA Weather Index |

**Important:** These are **not** the daily maximum or the last METAR at
KORD, KMDW, KLAX etc. Each is a minute-resolution composite of several
stations, published by **Kalshi**, in °F to **0.01** precision. The applicable
configuration may change. Kalshi's official index is fetched through
`GET /trade-api/v2/live_data/weather/{city}?last_sec=7200`.

The target is an ET **clock hour**. For a point-in-time `at` contract,
Kalshi's contract terms specify the latest canonical index observation with
event minute no more than 60 minutes before the target. It first becomes
available no sooner than 5 minutes after the event minute. Statuses normal
or degraded are eligible; backfilled/unsupported readings do not become
retrospective forecasts. Contract-specific rules always prevail.

## Predictor

WeatherEdge extrapolates the latest published Kalshi Index using a clipped
20–50 minute historical trend (0.55 damp factor). It uses a **heuristic**
uncertainty of `0.8 + 0.85 * sqrt(hours ahead)` degrees Fahrenheit, with
an extra 0.3°F for a degraded source minute. These coefficients are **not**
fitted to settled hourly contracts. It computes directional YES estimates for
**actual open threshold markets only**, where the metadata confirms they
reference the Kalshi Weather Index.

```
P(YES at x°F or above) = 1 - NormalCDF(x, mean, heuristic_sigma)
```

The probability is an uncalibrated research estimate, **not** an expected
profit or validated probability. No entry/exit orders are available.

The on-screen 90% interval is a Gaussian modeling assumption, not verified
coverage. The Weather Calibration page leaves metrics unmeasured until at
least 10 genuine matched hourly events have settled.

## Point-in-time and settlement evaluation

All sources are kept in *separate* tables in
`data/weather/weatheredge.sqlite3`:
- `weather_hourly_index`: first locally witnessed Kalshi minute-index point,
  UTC event time, local receipt time, reported status, config version and hash.
- `weather_hourly_markets`: current public event/threshold metadata and receipt.
- `weather_hourly_snapshots`: write-once **one preselected threshold** per
  city and event, before settlement (5–65 minutes in advance); selection is
  closest to a predicted 50% YES before outcome is known.
- `weather_hourly_settlements`: source index and Kalshi's final market result,
  the official index value, both original responses, hashes and check time.

Historical daily-high samples and model artifacts are preserved for audit but
are no longer collected, predicted, calibrated or surfaced in active Weather
pages. The old daily research CLI commands, where present, are archival only.

The system only scores an hourly market if its status is settled, the
canonical index minute is no more than 60 minutes before target, the reported
directional outcome agrees with the actual official index and market threshold,
and re-parsing the original saved source bytes reproduces the result.
Otherwise it remains unverified. Each hour is one sample, not a separate
independent sample per strike.

### What is **not** proven

There are no reported actual forward Brier scores, reliability claims,
profitability claims, or guarantees of 90% interval coverage as of this
change. A matched Kalshi index and its Kalshi contract outcome are two
sources of **exchange evidence**, not independent outside certification.
An unknown market rule or unavailable endpoint causes an explicit no-forecast,
not substitution of a nearby airport or invented price.

## Sources

- [Kalshi Get Weather Index](https://docs.kalshi.com/api-reference/live-data/get-weather-index)
- [Kalshi Get Weather Index Calibrations](https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations)
- [Chicago Metro Index contract terms](https://assets.kalshi.com/contract_terms/CHIMETROWINDEX.pdf)
- [Hourly temperature markets](https://kalshi.com/category/climate/hourly-temperature)
