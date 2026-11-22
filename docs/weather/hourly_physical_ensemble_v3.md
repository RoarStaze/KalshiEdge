# WeatherEdge V3 — Kalshi-calibrated HRRR and NBM physical guidance

## Goal

Forecast the four supported hourly Kalshi city Weather Indices using
public HRRR/NBM station-grid temperatures and Kalshi's actual published
configuration timeline. NOAA provides forecast guidance, not the
canonical HF-ASOS observations that determine Kalshi's settlement.
The physical model is therefore **research-only** pending independent
prospective validation. Live order placement remains disabled.

## Grounded source hierarchy

- Official Kalshi calibration history:
  https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations
  It provides city, Celsius units, station identifiers, base weights,
  offset_c, city_reference_c, effective_at_ms, published_at_ms, and
  config_version. Values are never hardcoded or replaced by guesses.
- Kalshi CFTC filings (methodology, timestamps, source/QC rules):
  https://www.cftc.gov/filings/ptc/ptc10012630774.pdf
  https://www.cftc.gov/filings/ptc/ptc10012630851.pdf
- NWS Aviation Weather Center ICAO station information:
  https://aviationweather.gov/data/api/
- NOAA HRRR and NBM public issue-time/valid-time GRIB guidance:
  https://rapidrefresh.noaa.gov/hrrr/
  https://vlab.noaa.gov/web/mdl/nbm-download

## Algorithm

1. Discover the next UTC hourly target and fetch the complete published
   Kalshi config timeline for each city.
2. Verify the city, Celsius units, strictly ordered effective times,
   distinct configurations and station IDs, finite positive weights
   summing to 1.0, and city_reference_c equal to the weighted full
   member offset. Select the version effective for target **only if**
   published no later than forecast time.
3. Keep immutable config hashes, source response digests and local
   collection timestamps; preserve this history if NOAA is unavailable.
4. Match each calibration 1M HF-ASOS member to its same ICAO airport
   identifier in the NWS Aviation Weather Center station registry.
   The airport coordinate is a GRIB grid sampling proxy. It is NOT
   certified as the exact HF-ASOS sensor coordinate.
5. Fetch one bounded recent HRRR cycle and one NBM cycle whose issue
   hour plus forecast lead equals the target hour. Use indexed GRIB
   byte-range retrieval, the ordinary 2-metre temperature field,
   actual decoder completion/receipt timestamps and raw GRIB hashes.
6. Sample both model grids at all configured airport coordinates.
   Missing members fail closed; do not fabricate a quorum, extrapolate
   missing grid data or silently use an unrelated weather model.
7. Convert Kelvin to Celsius, compute city reference B_c from **all**
   published member weights and offsets, then apply:
   adjusted station z_i = temperature_c_i - offset_i + B_c
   city index_c = sum_i(weight_i * z_i) / sum_i(weight_i).
   Convert once at final output to Fahrenheit.
8. Store both per-station, per-model research forecasts with the exact
   target, source model run, receipt, Kalshi version and hashes.
   The optional displayed equal-weight blend is explicitly UNTRAINED,
   not a certified probability or trade suggestion.
9. Run automatically under the WeatherEdge supervisor when users run
   kalshiedge start. A Kalshi/NOAA outage does not stop the core app.
   Show physical model status in the existing Weather forecast details.
10. Preserve the existing official index forecast, live YES/NO
    probabilities, confidence gating and paper-only trading status.

## Promotion and research gate

Do not activate physical output in the live predictor until timestamp-
correct frozen historical HRRR/NBM forecasts have sufficient externally
verified canonical index and settled contract outcomes. Compare exactly
paired city/lead/config observations under rolling-origin validation,
then with an untouched prospective test set for temperature MAE,
Gaussian CRPS, Brier/log loss, calibration and 90% interval coverage.
The correct threshold for success must be based on evidence, not a
higher displayed confidence percentage.

## Limits

NOAA grid air temperatures are estimates rather than the source
high-frequency observations used for Kalshi's quality-control and
fallback procedures. The calibration API does not provide exact
high-frequency sensor coordinates. No official source was found
establishing a live accuracy advantage for this physical ensemble.
The Weather model should say NO_PHYSICAL_GUIDANCE if a required
input is missing rather than fabricate temperatures or probabilities.
