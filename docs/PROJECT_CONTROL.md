# KalshiEdge Project Control

**Authoritative project state** — update this document after every meaningful milestone.

## Objective

Build the KXBTC15M Empirical Edge Engine whose single purpose is to discover and later exploit statistically validated, actually executable KXBTC15M calibration errors without overfitting or becoming predictable/exploitable.

## Non-negotiable rules

- Official current Kalshi/CF Benchmarks documentation overrides stale project assumptions.
- Never invent endpoints, fields, fees, limits, settlement rules, or capabilities.
- Preserve canonical Phase 1 raw data before deriving research features.
- Distinguish chart touch, trade touch, executable bid/ask touch, hypothetical fill, and actual fill.
- No hypothesis advances on p-value alone; economic significance and execution realism are mandatory.
- No martingale.
- Never claim a test/gate passed without evidence.
- Bootstrap model performance never bypasses the Phase 1 gate.
- Live order placement remains disabled until the later trading phase and a separate explicit authorization after a readiness report.

## Current state

- **Primary phase:** 1 — Data Foundation
- **Phase 1 gate:** **NOT PASSED** unless a fresh host-side report proves every acceptance condition below.
- **Phase 1 collector:** deployed independently on Windows as scheduled task `KalshiEdge Phase1 Collector`; the last host verification confirmed it running. Bootstrap implementation work does not stop, restart, reconfigure, or share mutable state with it.
- **Bootstrap implementation branch:** `feat/bootstrap-hybrid-predictor` until Task 10 PR/merge completes.
- **Bootstrap capability:** parallel read-only historical backfill, leakage-safe dataset construction, chronological structural/ML ensemble training, untouched lockbox promotion, and isolated live probability inference.
- **Task 9 isolated live inference:** implementation complete and verified in CI before Task 10.
- **Trading mode:** `READ_ONLY_DATA_COLLECTION` for canonical Phase 1 data; bootstrap live mode is `READ_ONLY_BOOTSTRAP_INFERENCE`.
- **Live-order authorization:** **NONE**.
- **Order placement/cancellation/portfolio mutation:** not implemented by the bootstrap predictor.
- **Phase 2 completion:** **NO**. The bootstrap predictor is a parallel evidence-generating capability, not a declaration that Phase 2 has begun or completed.

## Phase map

1. **Data Foundation** — immutable WS/REST collection, integrity, deterministic replay.
2. **Research Governance + Path Engine** — preregistered touch/path hypotheses and derived datasets.
3. **Untouched OOS / Lockbox Validation** — power, effect size, multiple testing, dependence, realistic fills.
4. **Shadow / Paper Live** — counterfactual execution, market/BRTI markouts, drift/adverse selection.
5. **Tiny Live** — disabled until separate authorization and readiness review.
6. **Capacity Scaling** — only after predefined live stability/capacity criteria.

The bootstrap hybrid predictor runs alongside this phase map. It may generate read-only predictive evidence sooner, but it does not advance or waive any phase gate by itself.

## Phase 1 architecture

- Public REST discovery for open `KXBTC15M` events/markets.
- Authenticated WebSocket subscriptions for orderbook, trades, BRTI, and market lifecycle.
- Schema-v2 raw envelope with source/receive timestamps, canonical payload hash, exact WebSocket wire hash/payload, connection ID, SID/SEQ, market/index identifiers.
- Credential-free collector-session snapshots capture package/git/runtime/non-secret configuration metadata for reproducibility.
- Immutable segment finalization using atomic rename and SHA-256 sidecar manifests.
- Separate immutable REST metadata snapshots for series/open events and settlement market records.
- Sequence gap/duplicate/out-of-order detection, malformed frames, and stale-feed timeouts fail close the active connection and force resubscription/fresh snapshots.
- The collector remains on BRTI + lifecycle channels even when no market is currently open, allowing it to observe first publication/creation before re-discovery.
- Deterministic order-book replay and dataset-wide gate verification.

## Parallel bootstrap predictor architecture

- All bootstrap historical/raw/derived/model/prediction outputs stay under `data/bootstrap/`; canonical Phase 1 `data/raw` is never a bootstrap target.
- Official Kalshi historical outcomes/settlements provide labels.
- Official Binance BTCUSDT 1-second history is an external feature source only, not a settlement-label substitute.
- Point-in-time rows enforce feature timestamps at or before each checkpoint and group all checkpoints from one market into one chronological split group.
- Training uses chronological development folds plus later calibration; the final lockbox is not used for feature selection, hyperparameter selection, stacking, or calibration.
- Model bundles are hash sealed with exact feature/schema/provenance/dependency/component metadata.
- Only the one-time evaluation/promotion path may touch the predeclared lockbox.
- Only a promoted/default, hash-verified bundle may be loaded for live inference.
- Live inference owns separate read-only Kalshi and public Binance streams, shares no mutable collector state, and emits `NO_PREDICTION` on stale/missing/inconsistent inputs.
- Final-minute structural inference uses official BRTI observations and fails closed on missing/duplicate/ambiguous settlement-window samples.

## Phase 1 acceptance gate

All conditions must be freshly evidenced before Phase 1 is declared passed:

- 24 continuous hours and at least 90 KXBTC15M windows captured.
- Every finalized raw segment passes SHA-256 verification.
- Zero unexplained sequence gaps, duplicate events, or out-of-order events in the accepted dataset.
- At least one clean reconnect/resubscription cycle observed and reconciled.
- Lifecycle transitions across multiple 15-minute rollovers captured.
- BRTI stream captured through final-minute windows, including required settlement-window observations/average evidence.
- Series/open-event metadata snapshots captured.
- Determined/settled market metadata snapshots captured for completed markets.
- Replaying the same accepted raw data twice produces identical derived order-book state hashes/fingerprint across all segment rotations.
- Collector restart does not mutate prior finalized raw segments.

A bootstrap model promotion does **not** satisfy these conditions.

## Bootstrap promotion gate

- Experiments are saved separately from promoted bundles.
- Training cannot create the live default and cannot materialize lockbox labels/features.
- The untouched lockbox is evaluated once per experiment hash.
- Promotion requires the predeclared out-of-sample rule against contemporaneous Kalshi probability, plus passing leakage/schema/ablation evidence.
- A failed experiment remains an experiment and cannot become `models/default.json`.
- A promoted model is permission for read-only inference only; it is **not live-trading authorization**.

## Risks and blockers

- **Phase 1 gate remains incomplete** until fresh acceptance evidence says otherwise.
- Historical/live API schema or contract changes can invalidate assumptions and must be re-verified against first-party documentation.
- Lifecycle/BRTI feeds can be noisy or interrupted; causal/final-minute inference must fail closed rather than impute settlement data.
- Raw storage can grow rapidly; retention/compaction must never overwrite canonical Phase 1 segments.
- A bootstrap candidate may fail the untouched lockbox promotion gate. If so, do not bypass the gate and do not start live inference with an experiment bundle.

## Exact next action

After Task 10 repository changes are merged and post-merge CI is green, perform the single authorized host-only bootstrap execution on the existing Windows clone:

1. update the local clone to the verified merged `main`;
2. preserve the ignored `.env` and RSA private key;
3. install `requirements.research.lock`;
4. run `bootstrap-backfill --source all`, `bootstrap-build-dataset`, `bootstrap-train`, and `bootstrap-evaluate`;
5. start `predict-live` only if the promotion gate produced a verified default model;
6. leave `KalshiEdge Phase1 Collector` untouched throughout.

Continue collecting Phase 1 evidence independently. There is **no live-trading or order-placement authorization**.