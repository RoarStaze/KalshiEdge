# WeatherEdge V15 — point-in-time HRRR/NBM fusion

## Why V15 exists

V13 beat the reconstructed V12 observation component across most of the 90-day
Index archive. V14's reversal adjustment was generally very small. To create
a substantially different challenger, V15 adds actual NOAA weather guidance
to the prior V13/V14 observation model, **not more Kalshi Index-only regressors**.

NOAA HRRR/NBM issue/valid times are operational forecast data, and both are
available from original NOAA archives:

- https://rapidrefresh.noaa.gov/hrrr/
- https://registry.opendata.aws/noaa-hrrr-pds/
- https://vlab.noaa.gov/web/mdl/nbm-download
- https://registry.opendata.aws/noaa-nbm/

The existing WeatherEdge physical collector reads these fields as NOAA GRIB2,
samples the Kalshi-configured member airports, computes the Kalshi Index from
the published **effective-and-known** weights and offsets, and saves actual
local NOAA run time, local received time and source digests.

## V15 model

The model starts with the actual pre-physical V13/V14 mean and Gaussian sigma.
It learns a **bounded, robust, ridge-shrunk** correction from:

1. Difference of HRRR/NBM average versus the V13/V14 forecast.
2. Signed disagreement between the HRRR and NBM forecast means.
3. Small residual bias.
4. Training-only Gaussian sigma-scale and NWP-disagreement uncertainty.

Coefficients are clipped and fitted only to **earlier** locally frozen
temperature outcomes. A confidence number from a later finalized Kalshi
market is never an input.

## Honest point-in-time benchmark

The original 90-day Kalshi Index archive does **not** contain timestamp-
certified corresponding NOAA forecasts originally available to the app.
Simply fetching NOAA GRIB files now and pretending they were received at an
old forecast deadline would introduce lookahead. V15 does **not** use that
retrospective data as if it were a live paired backtest.

Instead, the existing `weather_hourly_physical_pairs` collector stores the
first frozen live baseline forecast, matched HRRR and NBM Kalshi-calibrated
guidance, original forecast cutoff and respective model-receipt timestamps.
`collect_cases` validates the record digest, same exact calibration, both
NOAA issuance/receipt timestamps, and subsequently received official normal
Kalshi Index observation within ten minutes of the target. Exact settled
market results are counted only when the originally frozen ticker matches.

For each city, source configuration, lead bucket and unchanged V13/V14 base:

- 64+ causally available, earlier valid pairs used for fitting; last 24
  chronological, contiguous target hours are untouched for validation.
- The fitted V15 candidate must improve the same 24 hours by at least
  **0.05°F MAE and 0.025 CRPS**, with 90% Gaussian coverage ≥75% and no
  more than 10 percentage-point degradation from the incumbent.
- A fresh qualifying forecast must have both actual NOAA feeds received
  before the decision cutoff, matching published Index configuration,
  and recent source history (≤48 hours).
- Only then may V15 automatically replace the temperature estimate in
  `kalshiedge start`, with distinct immutable model identity
  `HISTORICAL_PAIRED_NOAA_FUSION_INDEX_V15`.
- An already-approved prospective, market-verified physical V4 lockbox
  takes priority over a V15 temperature-only improvement.
- When V15 does activate, every subsequent immutable physical-pair record
  continues freezing the contemporaneous, independently computed V13/V14
  *counterfactual* forecast so V15 can be re-evaluated without accidentally
  erasing its own training baseline. The actual forward forecast retains
  V15's separate model identity.
- If there is not enough data, an invalid receipt, no improvement or a
  changed baseline model, it retains the existing V13/V14 forecast.

V15 does **not** enable real weather orders or certify Kalshi betting
accuracy. The existing independent V4 physical lockbox and frozen History
remain separate; prior model lockboxes cannot be reused under V15's identity.

## Commands

Run ordinary collector and forecast supervisor as usual:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
kalshiedge start
```

Inspect accumulated exact local pairs and current readiness without
fetching or pretending to have 90 days of NOAA forecast releases:

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.weather.hourly_v15
```

For a machine-readable evidence report:

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.weather.hourly_v15 --json
```

All actual V15 superiority and market-accuracy claims remain **unknown**
until sufficient prospective pairs exist. A TDD synthetic winner proves
only that the implementation can detect an imposed predictive signal.
Weekly published Kalshi configuration updates, incomplete NOAA cycles
and genuine archive gaps can delay or prevent qualified comparisons.
