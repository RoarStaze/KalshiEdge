# WeatherEdge AI inside KalshiEdge

## Scope and safety

Weather appears under the top-left **MARKET** selector, alongside Bitcoin and Gold.

Weather pages:
- `/weather`: Forecast readiness (no numerical probabilities until independently validated)
- `/weather/train`: Training prerequisites (no button starts model fitting)
- `/weather/history`: Explicitly unverified performance
- `/weather/data`: Actual station/source collector receipt counts

`GET /api/weather/status` is read-only, does not create a database, and
returns `mode: PAPER_ONLY`, `real_orders_enabled: false`,
`forecast_available: false` and `settlement_verification: NOT_CERTIFIED`.
The external WeatherEdge package still owns collector/training/simulation.
KalshiEdge does not embed it or import its trading classes.

The reader uses a **fixed** database path relative to KalshiEdge's
`KALSHI_BOOTSTRAP_BOOTSTRAP_DIR` (default `data/bootstrap`):
`data/weather/weatheredge.sqlite3`. No API arguments can redirect that path.
Unexpected/missing/invalid SQLite databases produce safe status responses.
Only HRRR, NBM and METAR receipt counts are reported. They are not settlement
labels, calibrated forecasts, paper fills, or profit figures.

## Link the existing WeatherEdge v0.2.0 collector (Windows PowerShell)

Keep WeatherEdge in a separate environment. Extract the supplied WeatherEdge
v0.2.0 archive to `$HOME\Desktop\weatheredge-ai` and install its optional
GRIB dependencies in that project's venv:

```powershell
cd "$HOME\Desktop\weatheredge-ai"
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[grib]"

$weatherDB = "$HOME\Desktop\KalshiEdge\data\weather\weatheredge.sqlite3"
$stationJSON = "$HOME\Desktop\weatheredge-ai\examples\nwp_stations.json"

& .\.venv\Scripts\python.exe -m weatheredge collect-nwp `
  --stations-json "$stationJSON" `
  --models HRRR,NBM `
  --forecast-hours 1,2,3,4,5,6,7,8,9,10,11,12 `
  --db "$weatherDB" `
  --cache-dir "$HOME\Desktop\KalshiEdge\data\weather\nwp-cache" `
  --watch
```

Run METAR collection from a second terminal:

```powershell
cd "$HOME\Desktop\weatheredge-ai"
& .\.venv\Scripts\python.exe -m weatheredge collect-metars `
  --stations KORD,KDEN,KATL,KLAX `
  --db "$HOME\Desktop\KalshiEdge\data\weather\weatheredge.sqlite3"
```

The METAR command above is one-shot. Schedule or repeat it with an appropriate
rate limit as needed; it is not a continuous watcher. The standalone
WeatherEdge v0.2.0 HRRR/NBM collector has not yet been certified against live
NOAA GRIB downloads on Python 3.12.

Start the existing KalshiEdge dashboard normally and open `/weather/data`;
the read-only station receipts refresh every 15 seconds.

## Unverified work

This feature does **not** certify contract-specific settlement rules, enable
WeatherEdge model training in the KalshiEdge UI, or deploy a live weather
trader. Training and order submission require separate validation milestones.
No historical weather P&L, 30-day paper results or forecast accuracy is
fabricated by this integration.
