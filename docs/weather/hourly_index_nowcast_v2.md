# WeatherEdge: canonical-index walk-forward nowcasting

## Scope

Hourly market outcome: Kalshi's own per-minute configured **city Weather
Index** at the contract's settlement hour (or its explicitly permitted
fallback), not a single NWS/METAR airport temperature or daily maximum.
An unverified NOAA HRRR/NBM station-level forecast is not a substitute for
this exact weighted index. All new code is read-only / weather paper-only.

## Why this is the next improvement

The existing hourly predictor uses one most recent slope damped by 0.55
and assumes an uncertainty scale. Residual learning from precommitted,
settled Kalshi markets requires an eligible open contract, a saved
pre-event forecast and a source-reconciled settlement, so market-data
availability can prevent it from learning.

Minute index observations are already being recorded by `kalshiedge start`
with distinct UTC **event** and locally stamped **receipt** times.
Their own completed top-of-hour index values offer an additional,
more frequent *research* training stream even when historical contract
results are unavailable. They do NOT amount to independently audited
Kalshi contract settlement labels.

## V2 design

1. Reconstruct one historical point-forecast case per hour and each
   5/15/30/45/60-minute lead-time bucket.
2. A historical feature is eligible only if its local receipt timestamp
   was no later than its historical forecast cutoff. Exclude older
   backfills, invalid payloads, future timestamps, stale readings
   and other city-index configuration versions. Evaluate only exact
   top-of-hour publisher readings received within ten minutes.
   Do not impute settlement from a nearby airport or later restatement.
3. Train on the older cases to choose among predeclared persistence,
   fast trend, slow trend and blended trend candidates. Fit only a
   bounded standard-deviation scale using the **training** cases.
   Minimize Gaussian continuous ranked probability score (CRPS).
4. Lock the latest 24 hourly outcomes as chronological validation.
   A candidate is eligible only after at least 48 earlier hours and
   24 later validation hours, and only if:
   - MAE improves by at least 0.05°F
   - Gaussian CRPS improves by at least 0.03°F
   - candidate MAE is at most 2.5°F
   - nominal 90% interval coverage is at least 72% and loses no more
     than 15 percentage points relative to the incumbent baseline.
5. Only apply a learned nowcast if those requirements pass, matching
   live lead bucket/current index-version inputs exist, and the
   preexisting **verified-settlement residual learner** is not active.
   Never stack two independently trained corrections.
6. Return actual historical sample counts, model selection, lead-time
   and held-out scores in the read-only forecast payload. Display them
   in expanded technical details. Keep all evidence marked historical
   and uncertified; never imply that nominal 90% uncertainty is proven.

## Operational behavior

The existing hourly collector still runs automatically through
`kalshiedge start`. No additional services, API keys, or explicit
training command are needed for V2. A new installation with sparse
hourly-index history stays on the original forecast until enough
independent completed hours have accumulated. Historical performance
is **not** a prospective accuracy, probability calibration, or
profitability guarantee.

## Future work (not in V2)

- Versioned, official Kalshi city-member rosters and per-station
  weights, offsets and receipt/quorum/QC policies;
- point-in-time, rights-compliant station observation ingestion,
  rather than interpolating sparse hourly METAR as 1-minute HF-ASOS;
- HRRR/NBM grid-to-station matching, with true model issue/valid times,
  correct index aggregation, and published member availability;
- rolling-origin 30/60/90-minute physical+statistical ensemble
  evaluation against untouched prospective city-index outcomes and
  settlement-reconciled contract Brier/log-loss;
- documented performance and feed-latency based promotion/rollback.

## Primary sources

- Specific Weather Index terms and methodology:
  https://assets.kalshi.com/contract_terms/NYCWINDEX.pdf
- Kalshi's broader weather overview (distinct from city-index-specific
  rules): https://help.kalshi.com/en/articles/13823837-weather-markets
- NOAA HRRR: https://rapidrefresh.noaa.gov/hrrr/
- NOAA National Blend of Models:
  https://nomads.ncep.noaa.gov/txt_descriptions/BLEND_txt.html
