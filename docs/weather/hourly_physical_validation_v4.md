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
## Deployment-specific safeguards

- An eligible candidate must use at least 64 older paired, completed index hours and 24 subsequent holdout hours from the same city, lead bucket, Kalshi calibration configuration and latest contiguous incumbent baseline-model type.
- A candidate is fixed before the prospective lockbox. Only its first 32 **consecutive UTC target hours** after the lock count; an outage does not move the evaluation window forward.
- At least 24 of those 32 hours must have source-reconciled **the exact same Kalshi event, market ticker and threshold selected before target**. A different settled ticker in the same city does not establish this contract's probability outcome.
- Prospective MAE and Gaussian CRPS must both beat the paired incumbent; the same 24+ precommitted thresholds must have lower Brier and log loss, with a guarded nominal 90% interval coverage.
- The full evidence minimum is 120 completed hours per city, lead and configuration, including 32 truly subsequent hours and 24 matched market outcomes. Missing source data or weekly configuration changes can increase elapsed collection time or prevent automatic promotion entirely.
- Baseline model changes invalidate a lock built against an older baseline; new candidates can lock separately for the changed model type. No historic fitted coefficients are rewritten using lockbox outcomes.
- A passing lockbox is limited prospective evidence, not fully independent calibration certification or a guarantee that future probabilities will be accurate or profitable.
