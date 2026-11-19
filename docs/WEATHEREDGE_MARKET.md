# WeatherEdge AI: point-in-time forecast engine in KalshiEdge

## What is implemented

Weather in the top-left Markets selector now includes:
- `/weather`: **experimental live** station probability estimates, source receipts,
  current observed high, HRRR/NBM modeled peak temperatures, 90% Gaussian
  **model** interval and 1°F airport-proxy probability buckets.
- `/weather/train`: chronological station-training prerequisites.
- `/weather/history`: explicit lack of independently validated trading history.
- `/weather/data`: real count and last receipt per station/source.

Read-only APIs: `GET /api/weather/status`, `GET /api/weather/forecast`.
No weather trading POST routes or credential access are added. BTC and Gold
workspaces are unchanged. The synthetic preview never reads the real weather DB.

**Important:** Forecasted values are initially uncalibrated research estimates.
They are *not* Kalshi contract probabilities, independently verified settlement
outcomes, or proven profitable signals. Daily-high settlement sources, local
climate-day rules and revision windows must be checked **per ticker** before
using any estimate for trading. In particular, NOAA METAR or HRRR is not an
automatic substitute for The Weather Company when it is named in market rules.

## One-command startup (Windows PowerShell)

After updating the local repository **once**, weather collection starts
automatically with the normal KalshiEdge launcher:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
kalshiedge start
```

No second terminal, manual `weather-collect-metars` or
`weather-collect-nwp` command, or separate WeatherEdge installation is
necessary. The single launcher supervises the existing BTC dashboard and
predictor plus a **weather-only background worker**. It owns and shuts down
the weather worker on normal Ctrl+C or application exit; `kalshiedge test`
never starts collectors.

The weather worker immediately attempts current AWC METAR observations,
checking again every **five minutes**. Separately, it requests recent
NOAA HRRR/NBM GRIB forecast cycles, checking again every **30 minutes**.
Both write timestamp-correct records to `data/weather/weatheredge.sqlite3`.
The Weather forecast and Data pages display feed health from
`data/weather/collector-status.json` and read-only source counts.
The startup terminal stores detailed weather logs under
`data/bootstrap/diagnostics/app-weather-*.log`.

### Optional decoder setup on the first start

If `numpy`, `xarray`, `cfgrib` or `eccodes` is missing, the weather
worker attempts a **one-time background installation** of the declared
binary wheel dependencies, with no shell or manual intervention. This may
require network access to PyPI and available Windows wheels and can modify
the project's active Python environment. To disable automatic installs
in an environment managed by another package manager, set
`KALSHIEDGE_WEATHER_AUTOINSTALL=0` before starting the app.

If installation fails, METAR continues independently; HRRR/NBM show
`DEPENDENCIES_UNAVAILABLE`, with a reason in the Weather page and weather
log. Weather collection failures never stop the BTC dashboard or trigger
live orders. No future forecast data or fake values are synthesized.

### NOAA feed troubleshooting

The Weather page displays separate `METAR`, `HRRR`, and `NBM`
readiness indicators and the managed collector state. If NBM is missing,
valid METAR/HRRR values remain displayed, but no blend is published.

NOAA NBM core may include both ordinary
`TMP:2 m above ground` and ensemble-spread `TMP:2 m above ground`;
these are **not interchangeable**. The collector selects the deterministic
temperature field, verifies HTTP byte ranges and rejects ambiguous
inventories. A network failure, missing cycle, GRIB decoder issue or stale
forecast remains visible as a feed error.

Sources:
- https://registry.opendata.aws/noaa-hrrr-pds/
- https://vlab.noaa.gov/web/mdl/nbm-download
- https://aviationweather.gov/data/api/

## Inspect forecast status / point-in-time snapshot

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-forecast
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-forecast --station KORD

# Audit a historical instant (UTC, explicit offset required):
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-forecast `
  --station KORD --asof "2026-11-19T18:00:00+00:00"

kalshiedge start
```

Click **Markets > Weather > Forecast**. The visible probability table updates
every 15 seconds from `GET /api/weather/forecast`. The model initially uses
a transparent 50/50 HRRR/NBM high blend, an observation/trend adjustment and
an **assumed** 3°F Gaussian standard deviation. This assumption is not
calibrated and is not appropriate for autonomous real-money decisions.

Weather model output is labeled:
- `READY_RESEARCH`: fresh, complete observational and forecast inputs; research estimate available.
- `MISSING_DATABASE`, `MISSING_METAR`, `STALE_METAR`,
  `INCOMPLETE_HRRR`, `INCOMPLETE_NBM`, etc.: do not display a numerical forecast.
- `INVALID_MODEL`: unusable model artifact; no silent fallback.
- `STATION_BIAS_GAUSSIAN_RESEARCH`: fitted station-specific bias/sigma with
  an untouched chronological holdout, **not** third-party-certified calibration.

The climate-day calculation assumes local **standard** time, with a 3 PM
standard-time expected peak. That forecast-proxy convention is not a claim
about the rules for any particular Kalshi market.

## Build a labeled training dataset from actual recorded weather

Once real NOAA data exists for many independent days, prepare an evidence
manifest, one JSON object per station/day with the following fields:
`station`, `settlement_day`, `asof` (point-in-time snapshot UTC),
`actual_max_f`, `source_uri` (first-party publisher),
`source_sha256` (SHA-256 of captured publisher source bytes), and
`source_market_ticker` (specific Kalshi contract).

The exporter only accepts first-party source domains listed in its code.
It checks evidence formatting and that the saved historical NOAA inputs
were actually available at the specified `asof`. It **does not independently
authenticate** the publisher bytes, market rules or the accuracy of the
user-provided labels. You must review those sources separately.

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-export-training `
  --station KORD `
  --evidence-jsonl "data/weather/settlement_evidence.jsonl" `
  --out-jsonl "data/weather/verified_training_cases.jsonl"
```

The exporter reports skipped days when a historical point-in-time forecast
would have required unavailable NOAA data. Missing/late inputs are never
backfilled with observations acquired after the requested timestamp.
It does not manufacture historical training cases.

## Train station-specific research models

Training requires at least **60 distinct climate days** of retrospective
point-in-time features joined to reviewed official daily maximum labels.
The program does **not** invent training cases or fetch settlement truth
from an unrelated publisher.

Input: newline-delimited JSON objects, one independent day per station.
Every record needs:

```json
{
  "station": "KORD",
  "asof": "<UTC timestamp with explicit offset, from an actual saved snapshot>",
  "settlement_day": "<YYYY-MM-DD climate day>",
  "hrrr_max_f": "<real numeric stored model maximum, °F>",
  "nbm_max_f": "<real numeric stored model maximum, °F>",
  "observed_max_f": "<real numeric max-to-date, °F>",
  "trend_f_per_hour": "<real numeric airport trend>",
  "hours_to_peak": "<real numeric remaining hours>",
  "actual_max_f": "<real official publisher maximum, °F>",
  "label_source": "<specific first-party publisher evidence reference>"
}
```

The angle-bracketed fields above are **placeholders**, not real historical
records. Replace them with actual data. Training input labels and their
source claims are not automatically authenticated by this module.

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-train `
  --station KORD `
  --input-jsonl "data/weather/verified_training_cases.jsonl" `
  --min-days 60
```

The training command writes only a plain JSON artifact to
`data/weather/models/KORD.json`, never an executable pickle, and never
modifies BTC or Gold models. It fits a station-specific bias and training
residual standard deviation on the first 70% of independent chronological
days; the newest 30% are scored once as a holdout. Reports log loss,
multiclass Brier and MAE alongside a fixed uncalibrated baseline. It does
not silently promote to live execution if it improves those metrics.

Do not interpret lower held-out scores as a statistically significant
trading advantage. The next scientific milestone is source-specific label
verification, rolling out-of-sample forecasts, probability reliability
diagrams and the required 30-day forward paper trial.

## Project files

- `src/kalshi_edge/weather/collection.py`: AWC METAR observation receipts.
- `src/kalshi_edge/weather/nwp_collection.py`: NOAA GRIB2 indexed downloads.
- `src/kalshi_edge/weather/predictor.py`: point-in-time station inference.
- `src/kalshi_edge/weather/training.py`: chronological station training.
- `src/kalshi_edge/weather/dataset.py`: retrospective point-in-time feature/label export.
- `src/kalshi_edge/weather/research_ui.py`: Weather Forecast/Train/History/Data UI.
- `src/kalshi_edge/weather/status.py`: database health and counts.
- `tests/weather/`: data-integrity, API, calibration and route tests.

No real weather orders are ever submitted. Real Kalshi outcome-bin pricing,
fees, liquidity, and maker execution are not enabled by these commands.
