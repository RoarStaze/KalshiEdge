# WeatherEdge prospective calibration & evidence design

## Intended result
WeatherEdge can truthfully answer **how many independent days** were evaluated, whether nominal 90% intervals *actually covered* official daily high temperatures, and whether held-out probabilistic scores improve versus a fixed NBM reference. It must not replace absent observations with claims of calibration.

## Scope and interfaces
- `src/kalshi_edge/weather/prospective.py`: append-only point-in-time **forecast snapshots**, one per station per local-standard-time forecast hour, made by the already-running WeatherSupervisor. Never retroactively synthesize historical snapshots from freshly downloaded NWP archives.
- `src/kalshi_edge/weather/calibration.py`: validate local first-party publisher captures against SHA-256, declared market rules, station and climate day; evaluate **one precommitted noon standard-time forecast per station/day**, never choose the forecast with the smallest realized error. No independent certification can be inferred from manual claims.
- `GET /api/weather/calibration` read-only; live Forecast, Train and History tabs show sample counts, empirical 90% coverage and 95% Wilson uncertainty, multiclass Brier/log loss, comparative NBM metrics, and explicit evidence limitations. Until labels exist: **NO VALIDATED COVERAGE**.
- Existing `kalshiedge start` owns forecast journaling and collection; no extra user-run commands; no live orders or extraneous installs.
- Imported evidence is optional (records unavailable => `WAITING_FOR_SETTLEMENT_EVIDENCE`); no invented labels or P&L. Source documentation may conflict across contracts; match the market-specific documented publisher, do not silently accept a different source.

## Methodological guardrails
- One independent station/day at prespecified local-standard noon checkpoint (12:00–12:59), earliest successful recorded snapshot. Other periods are archived but not included in primary score.
- All NOAA features use *receipt timestamps* and model-cycle times; store the frozen forecast prediction, not recompute with later records.
- Require matching first-party source capture bytes, SHA-256 and documented ticker/source, with local reviewer attestation; still `MANUALLY_REVIEWED_NOT_INDEPENDENTLY_CERTIFIED` until live external contract/publisher reconciliation.
- For each station: include only validated independent days; no metric if fewer than 10 reviewed outcomes; no validated improvement claim if fewer than 60 independent reviewed days. Never score a model against its own training days as if prospective.
- Correctly score open-ended tails in the integer-bin forecast, 90% Gaussian interval coverage, MAE and proper multiclass Brier/log loss; Wilson confidence interval for coverage.
- No automatic promotion or live order permission. Multiple testing and untouched forward holdout required before asserting superiority.

## Test-first plan
- [ ] Create failing prospective-journal tests for write-once/local-noon eligibility, PIT timestamps, idempotency, error isolation, no synthetic data.
- [ ] Implement journal; integrate with WeatherSupervisor's 10-min loop.
- [ ] Create failing calibration tests for source capture checksum, path traversal, market-specific settlement source mismatch, prespecified noon day deduplication, zero-label status, empirical interval coverage and open-tail scoring.
- [ ] Implement calibration and evidence audit, paired NBM baseline metrics, Wilson 95% CI.
- [ ] Add read-only endpoint and UI, tests for preview safety and no fabricated calibration.
- [ ] Update docs, run full GitHub CI, review PR, merge to main only on success.
