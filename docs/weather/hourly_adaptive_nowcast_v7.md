# WeatherEdge V7 — accuracy-first adaptive Kalshi Index nowcaster

## Objective
Improve the actual next-hour **Kalshi Weather Index** temperature prediction, rather than UI/market quoting. The existing five hand-specified fast/slow trend models underfit city-specific trends. Add a source-time-valid, robust regularized learned nowcast candidate, trained and scored on independently published historic minute-index observations. It updates the normal forecast and YES/NO probabilities if and only if the same-hour chronological holdout demonstrates materially better MAE, Gaussian CRPS, and acceptable 90% interval coverage versus the incumbent baseline. Weather orders remain disabled.

## Sources
- Only Kalshi's published, locally received minute Weather Index observations from `weather_hourly_index` are training features/labels. No settlement outcomes, future readings, test-hour labels, unverified NOAA interpolations or values invented by the assistant.
- Existing HRRR/NBM physical forecasts remain independent. This milestone strengthens the actual deployed observation-nowcast path without claiming their unvalidated 50/50 mix is better.

## Model
- Continue the existing per-city/per-index-config/per-5/15/30/45/60-minute lead target and capped fast/slow slopes.
- Add `OBSERVATION_ADAPTIVE_V3`, trained on older chronological labeled hourly cases with an L2-regularized, robust Huber regression:
  target-current ≈ hours_to_target × (fast_gain × fast_slope + slow_gain × slow_slope + bias_per_hour).
- Clamp fitted gains, sum of gains, drift and uncertainty scale to physically reasonable ranges; use train-only residuals for sigma.
- Existing fixed candidates remain, and new adaptive regression competes on train-set Gaussian CRPS. A fixed untouched 24-hour time holdout determines whether the winner is allowed to change the live predictor.
- Store the coefficients and training/holdout scores in ordinary Weather forecast result for audit; no claim that synthetic tests prove real-world improvement. If no eligible history or scores fail, use the baseline unchanged.
- Daily actual forecasting may be better with physical innovation guidance, but that is a separate evidence requirement and is not represented as verified by this implementation.

## Acceptance
1. Synthetic source-time-valid examples for which static trend weights are inadequate select the learned candidate and the live temperature changes in the expected direction.
2. Changing only holdout outcomes cannot change fitted coefficients, sigma or chosen candidate. Holdout degradation stops activation.
3. Outliers and strongly correlated fast/slow slopes yield bounded finite coefficients; missing/corrupt observations fail closed.
4. Existing source-time, city-config, lead and weather paper-only gates remain.
5. Full GitHub Actions suite + container build and code review must pass before merge to main.