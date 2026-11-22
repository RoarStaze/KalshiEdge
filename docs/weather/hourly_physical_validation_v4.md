# WeatherEdge V4 — Paired physical ensemble validation and promotion

## Goal
Collect frozen point-in-time HRRR, NBM and existing WeatherEdge forecast predictions for the same hourly Kalshi city index, compare against later verified index and market evidence, and promote only after a fixed prospective lockbox demonstrates improvement.

## Inputs and time rule
- V3 physical records include calibrated city forecasts, source NOAA cycle/valid hour, Kalshi config version/hash, original GRIB digests and locally observed receipt times.
- Freeze one pair per city/target/lead bucket (5/15/30/45/60 minutes) only when both NOAA models and official index were genuinely available to the local process before cutoff.
- Never write a target observation in a frozen forecast. Score against a first-stored exact target-hour canonical index reading with normal status, same index config, local receipt within 10 minutes; reject missing/delayed/revised records.
- Use the existing independently reconciled hourly market settlement evidence to certify threshold outcomes, distinct from index-only research.

## Model
- Select nonnegative convex baseline/HRRR/NBM weights, bounded bias and Gaussian error scale on at least 64 older paired hours. Evaluate fixed parameters on next 24 hours and require MAE, Gaussian CRPS and interval coverage safeguards.
- Lock model coefficients immutably per city/index-config/lead. Never refit them on the untouched lockbox.
- Evaluate exactly the first 32 later distinct target hours. Require at least 24 independently reconciled settled Kalshi index/market hours with a precommitted threshold. Require MAE, CRPS, Brier and log-loss improvements plus reasonable 90% interval coverage on identical paired observations.
- Activate only with a current, matching physical config and lead; otherwise keep the existing forecast. Keep weather orders off and calibration_certified false without independent certification.

## Code boundaries
- New module src/kalshi_edge/weather/hourly_physical_learning.py owns immutable pairs, candidate locks, training and validation.
- hourly.py adds raw pre-physical model fields and a strictly gated optional physical correction.
- runtime.py freezes pairs and advances immutable locks through kalshiedge start.
- hourly_ui.py adds measured outcomes, candidate/lockbox status in technical details.
- Tests cover chronology, tampering, duplicate target hours, missing canonical observations, config changes, and nonpromotion.

## Limitations
NOAA forecast-grid samples are not independently verified HF-ASOS sensor observations and Kalshi settlement uses separate QC. Synthetic unit tests prove software behavior only, not an empirical accuracy advantage. See https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations and https://rapidrefresh.noaa.gov/hrrr/.