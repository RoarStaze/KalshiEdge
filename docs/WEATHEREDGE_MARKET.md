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

## Installation (Windows PowerShell, project root)

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

# Install optional NOAA GRIB2 decoder into the existing KalshiEdge environment.
& .\.venv\Scripts\python.exe -m pip install -e ".[weather]"
& .\.venv\Scripts\python.exe -m pytest -q tests/weather
```

Optional GRIB dependencies: xarray, cfgrib, ecCodes, numpy. Standard KalshiEdge
startup does not require those packages. If ecCodes fails to install on Windows,
inspect the optional dependency error before retrying; do not modify a running
training environment blindly.

## Start collectors in two separate foreground terminals

METAR observation collection (every five minutes):

```powershell
cd "$HOME\Desktop\KalshiEdge"
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-collect-metars --watch
```

NOAA HRRR/NBM model forecasts (once per hour):

```powershell
cd "$HOME\Desktop\KalshiEdge"
& .\.venv\Scripts\python.exe -m kalshi_edge.cli weather-collect-nwp --watch
```

Both write append-only records under `data/weather/weatheredge.sqlite3`.
The weather dashboard reads the database; it does NOT automatically launch
the collectors. These are foreground processes stopped with Ctrl+C.

The NWP collector requests 2-meter temperature fields through exact NOAA
GRIB2 byte ranges using the official HRRR and NBM S3 archives:
- https://registry.opendata.aws/noaa-hrrr-pds/
- https://vlab.noaa.gov/web/mdl/nbm-download

METAR observations come from:
- https://aviationweather.gov/data/api/

NWP cycles may be unavailable during publication delays. A failed or
incomplete cycle causes an explicit source failure, never invented data.
As-of queries use both source event time and actual reception time.
A recent incomplete NWP cycle cannot be combined with a prior cycle.

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
- `src/kalshi_edge/weather/research_ui.py`: Weather Forecast/Train/History/Data UI.
- `src/kalshi_edge/weather/status.py`: database health and counts.
- `tests/weather/`: data-integrity, API, calibration and route tests.

No real weather orders are ever submitted. Real Kalshi outcome-bin pricing,
fees, liquidity, and maker execution are not enabled by these commands.
