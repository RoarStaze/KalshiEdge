# Eight-minute prospective cohort fairness correction — 2026-11-02

## Observation
The frozen 480s specialist has three complete post-freeze snapshots and one additional verified incumbent forecast on market `KXBTC15M-26NOV020630-30` with **no shadow file**. That missing file is not a valid specialist SKIP decision; its cause is unknown. The original conservative 4-market denominator rightly exposes missing instrumentation but also masks a one-correct-forecast specialist-only gain on the three observed markets.

## Design
Keep the existing settled-all-after-freeze denominator and all metric names unchanged, including missing snapshots as nonqualifying for the specialist. **Add**, never replace, a `matched_snapshot_cohort` with all verified settled markets that have a valid, hash-checked shadow observation with matching timestamp/model identity. Include explicit `status=SKIP` shadow records in this denominator; exclude missing or corrupt snapshot files and count them separately. Score the *actual* original final decision, never just a recomputed hypothetical incumbent gate. Score the frozen specialist's unchanged 69% threshold. Also display missing-shadow market tickers to make instrumentation gaps auditable.

This is a paired *observability* cohort, not a statistically random sample. Its apparent lift must never replace conservative all-market coverage, and no model promotion, threshold change, order execution, or backfill is authorized.

## Test sequence
1. Add a regression using three settled markets and one pending: one paired candidate-only win, one paired explicit specialist SKIP, and one missing shadow where original AI qualifies; require matched cohort denominator of two and conservative denominator three.
2. Run CI; witness missing matched cohort assertion fail.
3. Add read-only accounting in `prospective_shadow_480.py`; no write or live code touched.
4. Re-run full CI and production build; inspect PR diff; only then merge.
