# WeatherEdge V12 — settle-recovery + gated diurnal candidate

## Verified code root causes (2026-11-27)
1. Finalized outcome recovery in `hourly_market_results.py` only attempted a seven-day window, despite the history table retaining older frozen forecasts. The loop considered up to 250 HTTP requests and could exceed the 180-second supervisor budget, repeatedly leaving old markets unresolved.
2. History showed a generic VERIFYING even when the official API request failed or official evidence was rejected; no persistent per-ticker diagnostic existed.
3. The model family used short fast/slow Kalshi Index slopes but no prior-day same-clock temperature-change candidate.
4. Historical accuracy measures a preselected threshold nearest model 50/50, not calibrated temperature prediction skill. Replacing that evaluation retrospectively or cherry-picking easy thresholds would be misleading.

## Settlement fix
- In the existing five-minute `kalshiedge start` worker, attempt at most 32 distinct unresolved frozen market tickers per pass.
- Reserve half the batch for newest results and half for oldest; deduplicate V9 vs legacy ticker targets.
- Collect concurrently with bounded HTTPX timeouts and max 4 in-flight official GETs.
- Search 90-day saved forecast horizon; use official `/markets/{ticker}` then documented `/historical/markets/{ticker}` fallback on 404. Do not invent outcomes from models or local thermometers.
- Preserve immutable first official final YES/NO response with SHA256 and exact source/status/settlement timestamp/threshold verification. Retain independent Kalshi Index reconciliation as stronger tier.
- Store mutable last-check diagnostics separately: AWATING_FINALIZATION, FETCH_ERROR, INVALID_OFFICIAL_EVIDENCE, VERIFIED, with checked timestamp and attempt count.
- History surfaces finalization state separately from feed/validation failure. No API response or invalid evidence may be counted as correct/incorrect. Leave older than 90-day data as pending rather than inventing a settlement.

## Model challenger
- Independently estimate same-clock Index temperature changes from prior 1–2 days, each sourced from properly received and timely weather-index observations available before forecast time.
- Fit a ridge-shrunk, bounded analogue gain and Gaussian uncertainty scale on historical training only, with at least 80 eligible training cases and 24 untouched latest validation hours.
- Compare against the previous V7 model on exactly the same validation hours; require MAE gain >=0.05°F, CRPS gain >=0.02 and coverage loss <=10 percentage points. Also retain existing baseline activation gate.
- If no historical skill, sufficient training hours or current analogue feature, continue using the prior model. Distinct model version isolates old physical ensemble lockboxes.
- Do **not** assert a real accuracy improvement without local forward validation. Synthetic tests demonstrate gating mechanics, not live superiority.

## Evidence limitations
- GitHub unit/CI tests cannot access the user's local SQLite dataset or their actual Kalshi API responses.
- Continued VERIFYING may be genuine exchange delay; settled market may contain missing or invalid data, in which case show explicit diagnostics and leave unverified.
- No weather orders or model promotions based solely on an in-sample headline percentage.
