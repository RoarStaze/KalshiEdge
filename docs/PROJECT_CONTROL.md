# KalshiEdge Project Control

**Authoritative project state** — update this document after every meaningful milestone.

## Objective

Build the KXBTC15M Empirical Edge Engine whose single purpose is to discover and later exploit statistically validated, actually executable KXBTC15M calibration errors without overfitting or becoming predictable/exploitable.

## Non-negotiable rules

- Official current Kalshi/CF Benchmarks documentation overrides stale project assumptions.
- Never invent endpoints, fields, fees, limits, settlement rules, or capabilities.
- Preserve canonical raw data before deriving research features.
- Distinguish chart touch, trade touch, executable bid/ask touch, hypothetical fill, and actual fill.
- No hypothesis advances on p-value alone; economic significance and execution realism are mandatory.
- No martingale.
- Never claim a test/gate passed without evidence.
- Live order placement remains disabled until Phase 5 and separate explicit authorization after a readiness report.

## Current state

- **Phase:** 1 — Data Foundation
- **Gate:** NOT PASSED
- **Implementation branch:** `feat/phase1-data-foundation`
- **Trading mode:** `READ_ONLY_DATA_COLLECTION`
- **Live-order authorization:** NONE
- **Current verified unit/integration status:** local test suite and synthetic end-to-end replay/integrity checks pass; live production validation has not been run because credentials are not available in this execution environment.

## Phase map

1. **Data Foundation** — immutable WS/REST collection, integrity, deterministic replay.
2. **Research Governance + Path Engine** — preregistered touch/path hypotheses and derived datasets.
3. **Untouched OOS / Lockbox Validation** — power, effect size, multiple testing, dependence, realistic fills.
4. **Shadow / Paper Live** — counterfactual execution, market/BRTI markouts, drift/adverse selection.
5. **Tiny Live** — disabled until separate authorization and readiness review.
6. **Capacity Scaling** — only after predefined live stability/capacity criteria.

## Phase 1 architecture

- Public REST discovery for open `KXBTC15M` events/markets.
- Authenticated WebSocket subscriptions for orderbook, trades, BRTI, and market lifecycle.
- Schema-v2 raw envelope with source/receive timestamps, canonical payload hash, exact WebSocket wire hash/payload, connection ID, SID/SEQ, market/index identifiers.
- Credential-free collector-session snapshots capture package/git/runtime/non-secret configuration metadata for reproducibility.
- Immutable segment finalization using atomic rename and SHA-256 sidecar manifests.
- Separate immutable REST metadata snapshots for series/open events and settlement market records.
- Sequence gap/duplicate/out-of-order detection, malformed frames, and stale-feed timeouts fail-close the active connection and force resubscription/fresh snapshots.
- The collector remains on BRTI + lifecycle channels even when no market is currently open, allowing it to observe first publication/creation before re-discovery.
- Deterministic order-book replay and dataset-wide gate verification.

## Phase 1 acceptance gate

All conditions must be evidenced before Phase 2 begins:

- 24 continuous hours and at least 90 KXBTC15M windows captured.
- Every finalized raw segment passes SHA-256 verification.
- Zero unexplained sequence gaps, duplicate events, or out-of-order events in the accepted dataset.
- At least one clean reconnect/resubscription cycle observed and reconciled.
- Lifecycle transitions across multiple 15-minute rollovers captured.
- BRTI stream captured through final-minute windows, including quarter-hour average fields.
- Series/open-event metadata snapshots captured.
- Determined/settled market metadata snapshot captured for completed markets.
- Replaying the same accepted raw data twice produces identical derived order-book state hashes/fingerprint across all segment rotations.
- Collector restart does not mutate prior finalized raw segments.

## Risks

- Missing or stale authenticated WebSocket credentials block live Phase 1 validation.
- Lifecycle channel is unfiltered and can be noisy; processing must stay non-blocking.
- Network loss near rollover can cause irreplaceable gaps; any gap invalidates that interval for strict research use unless independently reconstructed and explicitly labeled.
- Raw storage can grow rapidly; retention/compaction must never overwrite canonical segments.

## Exact next action

Configure a Kalshi API key locally or as a runtime secret and run `kalshi-edge collect` continuously. After the validation window, run `kalshi-edge verify-dataset <data_dir>` and `kalshi-edge phase1-report <data_dir>`. Do not start Phase 2 until the Phase 1 gate above has evidence.
