# Prospective 480s paired outcome monitor — implementation plan

> For agentic workers: use superpowers test-driven-development and verification-before-completion.

**Goal:** Read and score *only* frozen-model live shadow observations and original 480s final forecasts against verified official Kalshi settlements, without modifying forecasting or trading.

**Inputs:** Existing exclusively written final forecasts, `research/480_specialist/live_shadow/<UTCday>/<ticker>.json`, `final_outcomes/<UTCday>/<ticker>.json`, and the locally frozen model `research/480_specialist/frozen.json`. Never use training rows, historical calibration for this new report, live exchange credentials, or backfilled probability estimates.

**Boundary:** All observed market decisions must be eight minutes (480 seconds) and scheduled strictly after frozen `frozen_at_ns`; both forecasts must refer to exactly the same market, scheduled and observed timestamp, incumbent hash and frozen hash. Duplicate/conflicting or corrupt records must not be scored. No settlement means pending, not success or failure.

**Denominator:** Every verified, officially settled 480s final forecast after model freeze counts in both models' coverage denominator. Missing shadow, invalid shadow and explicit shadow SKIP count as non-qualifying, not silently excluded; incorrect/absent official settlements remain unscored with separate integrity counts.

**Metrics:** Each model's qualified count, qualified correctness, selected accuracy, skip count, coverage. Brier and log-loss scored only where a valid numeric prediction existed; compare Brier/log-loss on the *same* paired subset, never compare unmatched sample scores as model superiority. Provide matched gate cohorts as a separate diagnostic. Distinguish status OK from gate_qualified.

**Promotion:** No automatic promotion, re-fitting, threshold optimization or AutoTrade decisions. A research readiness threshold (default 80 settled markets) is descriptive only; actual deployment requires stronger independent uncertainty and operational metrics.

## Tasks
1. Write synthetic fixture tests for two valid matching settled forecasts; gate-qualified difference, proper scores, no-file mutation.
2. Run CI to demonstrate missing report implementation fails collection.
3. Implement `bootstrap/prospective_shadow_480.py` pure scoring, safe file discovery and CLI, reuse existing strict official settlement validator and existing shadow hash verification.
4. Test pending markets, missing/explicit SKIP shadow, tampering, invalid settlement, frozen hash mismatch, pre-freeze and legacy records.
5. Run complete repository CI and container build; review diff; merge only if successful.

**Non-goals:** The scorer does not query Kalshi or fetch outcomes; existing settlement sync process is responsible for official saved outcomes. Read-only output is JSON to stdout.
