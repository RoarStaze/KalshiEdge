# WeatherEdge hourly-index accuracy program — November 22, 2026

## Goal and objective function
Improve **settlement-specific** hourly Kalshi Weather Index forecasts, not airport daily highs.
Optimize point forecast MAE, event-threshold Brier score/log loss, calibration and
coverage. Reliability and correct observation/receipt timestamps are mandatory.
Do not infer profitable trades from forecast accuracy. Weather orders remain off.

## Verified baseline before this change
The active hourly predictor linearly extrapolates the newest official Kalshi
minute index from one historical slope, with fixed 0.55 trend damping and an
assumed Gaussian scale (`0.8 + 0.85 * sqrt(hours_ahead)`). It has no trained
city/hour bias or calibrated threshold distribution. The legacy HRRR/NBM
collector stores airport predictions, but they are not equivalent to the
Kalshi **weighted multi-station Weather Index** settlement underlying.

## Design decision and alternatives
- **Chosen first:** automatic source-audited residual postprocessing on the live
  index forecast. Fit a regularized city-specific bias/trend correction and
  variance scale *only* from prior frozen forecasts with independently
  reconciled, settled contracts. Compare against the untouched baseline on
  a chronological holdout; activate the correction only if MAE, Brier and log
  loss all improve beyond explicit thresholds. No external keys, no order APIs.
- **Rejected now:** blind insertion of HRRR/NBM 2-m temperature at a nearby
  airport as the Kalshi index. Different station panels, weights, offsets,
  timestamp windows, and receipt delays make this a target mismatch.
- **Rejected now:** an untrained neural network/LLM or nominal 90% uncertainty
  label. Complexity without station-specific labels does not provide skill.

## Stage 1 implementation
- Verify snapshot and settlement source digests, official canonical index
  reading, matching settled market, point-in-time availability and outcome.
- Group by city and target hour; reject invalid, late or tampered training labels.
- Fit bias and trend coefficient on older observations; shrink fit parameters
  toward the incumbent and bound their magnitudes.
- Evaluate the final 24 hours chronologically, with at least 64 earlier hours
  for training. Require improvements of >=0.03 F in MAE, >=0.003 in Brier
  and >=0.001 in log loss, all on the same observed contracts. Also require
  candidate holdout MAE <=2.5 F to reject catastrophic distribution shifts.
  Also require 90% candidate interval coverage >=75% and not more than 15
  percentage points below the baseline. This is a conservative small-holdout
  safeguard, **not** a claim of 90% calibrated coverage.
- Keep the original baseline if any gate fails. Report audit/holdout state and
  number of verified hours. Historical holdout improvement is **not**
  prospective accuracy certification; no empirical 90% coverage guarantee.
- Preserve raw forecast fields inside frozen snapshots so future evaluation
  compares models against identical historical cases.

## Stage 2 — correct physical ensemble inputs
Retrieve the effective *city-specific* Kalshi Weather Index methodology and
station configurations, including weights, offsets, source priorities and
version changes. Persist those configurations with effective times and their
hashes. Ingest 1-minute station data with local receipt times where authorized
and source QC; this must not be silently replaced with hourly METAR.
Join contemporaneous HRRR and NBM **forecast issue time and valid time** for
each member, then map each model's station forecasts into the exact weighted
city index. Absent a compatible configuration/source, mark the model missing.

## Stage 3 — historical training and promotion
Collect enough historical point-in-time index, constituent-station and NWP
forecasts to fit city-specific robust bias, lead-time volatility, diurnal and
weather-regime effects. Evaluate persistence, current baseline, physical
ensemble, regularized regression and gradient boosting using rolling-origin
blocked time splits with a locked final untouched test set. Compare strictly
proper threshold probability scores and 90% interval coverage by city,
lead-time, market liquidity and weather regime. Promote only with meaningful
out-of-sample evidence; rollback on feed staleness, drift or worse scores.
Never use publication timestamps as substitutes for actual local receipt.

## Validation boundaries
- Synthetic unit fixtures demonstrate code logic, **not** real-world gain.
- Production is not available for independent certification until enough
  genuine prospectively frozen and source-reconciled contracts settle.
- The official Kalshi index methodology fixes station membership, timing and
  canonical outcome; it must be followed exactly rather than inferred.
- The user should continue to launch the entire system with `kalshiedge start`.

## Primary research sources
- NOAA HRRR operational system: https://rapidrefresh.noaa.gov/hrrr/
- NOAA NBM documentation: https://blend.mdl.nws.noaa.gov/nbm-documentation
- NOAA MADIS quality control: https://madis.ncep.noaa.gov/madis_sfc.shtml
- CFTC Weather Index methodology filings: https://www.cftc.gov/filings/ptc/ptc10012630830.pdf
- Kalshi weather markets: https://help.kalshi.com/en/articles/13823837-weather-markets

## Review focus
1. Corrupted source hashes -> refuse training, preserve baseline.
2. Settlements checked after asof -> inaccessible, no look-ahead.
3. Reused/fabricated labels -> one verified snapshot per city/hour.
4. Candidate better at MAE but worse at Brier -> not deployed.
5. Few verified hours -> baseline retained and evidence count reported.
