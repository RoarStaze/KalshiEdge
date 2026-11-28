# WeatherEdge V13 promotion → V14 challenger

## Automatic, temperature-only model promotions

After upgrading to this release, `kalshiedge start` reads the real local
research outputs in the normal WeatherEdge data folder:

- `data/weather/historical-backtest-v14.json` — Chicago V12-observation vs V13;
- `data/weather/historical-backtest-remaining.json` — NYC, LA-coastal and Miami;
- `data/weather/historical-backtest-v14-vs-v13.json` — future V13 vs V14.

The latest November 28 source-audited, historical-index-reconstructed reports
favor V13 in 18 of 19 scored city/lead comparisons. NYC 15-minute MAE regressed
and NYC five-minute lacked scored hours, so those specific historical comparisons
cannot authorize V13 selection. The winning evidence remains **uncertified**:
historical publication-time reconstruction assumes a ten-minute receipt lag.
This evidence does NOT establish original live-pipeline superiority, verified
YES/NO accuracy, positive trading edge, or profitability.

A winner is eligible only if its reconstructed **paired** MAE and CRPS are
strictly lower, interval coverage remains acceptable, there are at least
72 matched 24-hour held-out cases, at least two CRPS-winning folds, and
recorded official source/calibration provenance. Invalid, stale, contradictory,
or unavailable reports fail closed. This is a research temperature promotion
policy, NOT the stronger market-verified promotion gate.

The production nowcast refits the selected version from real timestamped
LOCAL receipt-safe Index examples from the current live calibration regime.
At least 64 original-source, receipt-safe local historical cases with
adequate diurnal diversity, a present live slope and prior-day diurnal
feature are needed to fit and activate an archive-qualified V13 or V14
forecast. Prior to 96 local cases, this is explicitly marked
`ACTIVE_ARCHIVE_EVIDENCE_ONLY` (no separate local holdout). Once 96+
local cases exist, a 24-hour untouched local safety holdout checks
whether the promoted candidate regresses against the live observation
incumbent; a meaningful regression rolls back to the incumbent.
If there is insufficient recent data, the model remains the existing
observation incumbent with an explicit waiting reason. The historical
archive is NEVER inserted into the live receipt evidence database.
The existing physical blend/settlement pipeline stays separate.

## V14 methodology

V14 is a separately versioned model `REGIME_RESIDUAL_HYBRID_V14`.

It builds from an identically fitted V13 mean/sigma, adding:
- one bounded, ridge-shrunk nonlinear turning-point correction when fast and
  slower hourly temperature trends disagree in sign;
- an uncertainty multiplier triggered by the magnitude of slope disagreement,
  with its coefficient chosen on prior training CRPS only.

No market outcome or held-out future observation is used as a model feature.

**To run the true V13 vs V14 study using cached 90-day archive observations:**

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.weather.hourly_historical_backtest `
  --days 90 `
  --end-utc 2026-11-28T02:00:00Z `
  --challenger v14 `
  --request-interval-seconds 2 `
  --output "data\weather\historical-backtest-v14-vs-v13.json"
```

This uses the same city/lead source windows and the same local verified raw-byte
archive cache as the previous comparison. Whenever V14 beats V13 across a city/
lead in both MAE and CRPS with adequate held-out coverage and folds, the same
versioned local observation promotion can select V14 after live-data readiness.
If V14 fails, V13 stays active. This comparison does not place orders or modify
the historical market History results.

### Reproducibility and limitations

The V13 vs V14 run must be performed against the user's saved archive cache;
GitHub CI has only synthetic fixtures. A new version winning on the SAME
historical weather is a research improvement, but independent prospectively
frozen market forecasts and exact reconciled Kalshi settlement outcomes are
still necessary to validate odds/trading performance.
