# Prospective immutable 480-second feature evidence — November 3, 2026

## Goal and established evidence
The live 480s incumbent issued 55/153 forecasts, 53 correct (96.36% conditional accuracy); specialist issued 70/153, 61 correct (87.14%). On 152 fully matched markets the 18 specialist-only decisions were 11 right / 7 wrong. Previous research models have repeatedly failed the joint accuracy/coverage objective on historical reused datasets. **Do not lower the production confidence gate, promote specialist, or claim a forecasting improvement.**

KalshiEdge already stores (a) immutable final decision, (b) official settlements, (c) optional prospective frozen-specialist shadow, (d) mistake_pool with *labels* but not point-in-time feature vectors. The missing engineering component is reliable, label-free, immutable feature evidence at each NEW production 480s decision.

## Contract
- Capture only after the authoritative final decision has been saved; never delay its save or alter trade execution. Attach to live predictor's after-final research hook, in a try/except that cannot influence forecast/AutoTrade.
- For each new final, create exactly one **label-free** immutable JSON snapshot under `research/480_forward_features/<UTC-close-date>/<ticker>.json`; hash the canonical snapshot payload and bind it to raw immutable final-forecast SHA256. A second identical save is idempotent; conflicting duplicate is rejected.
- Include checkpoint timestamp, scheduled/close timestamp, ticker, original model identity, optional full causal numeric feature vector and source maximum timestamps. Explicit availability status if a healthy feature row cannot be built; no fabricated data. The existing `LiveFeatureState.feature_row` contains a fabricated `label_yes=0` solely for structural compatibility: **never serialize or use it**. No settlement/outcome/correctness fields are permitted in snapshot.
- Reject early/late checkpoints, mismatched final file, wrong ticker, future-source timestamps, invalid/NaN/infinite/boolean feature values, unsafe paths, malformed hashes, tampered snapshot files, unsupported 240s checkpoints.
- Separate read-only audit joins snapshot, stored final decision, and VERIFIED official Kalshi settlement **after** settlement. Count genuine issued correct/incorrect, skipped due low confidence (counterfactual direction kept separate), unhealthy skips, missing/invalid feature snapshot, pending outcomes, and feature-available examples. Report coverage denominators and version; no threshold/model tuning.
- Provide a `verified_forward_feature_examples(root)` pure loader producing FeatureRow instances ONLY from verified original label-free snapshots + authoritative official settlement; never from placeholder result. No reserved lockbox access or model artifact writes.
- Older 153 markets necessarily lack forward snapshots because snapshots cannot be retroactively recreated; explicitly report non-backfill and never claim missing snapshots are new candidate SKIPs.
- No auto-training and no live model changes. A challenger built later from this supervised data requires frozen pre-registration and subsequent unseen prospective data.

## TDD tasks
1. Add tests first for create-once/hash-bound snapshot, no label, source timestamps, invalid/unsafe inputs, idempotent/restart conflict, old 240s exclusion, post-settlement join with incorrect and skipped cohorts, no settlement => no labels, tampered inputs rejected.
2. Confirm test RED in GitHub Actions due to missing module.
3. Implement `src/kalshi_edge/bootstrap/forward_feature_evidence_480.py`, with CLI `python -m kalshi_edge.bootstrap.forward_feature_evidence_480` and no write effects except live capture through explicit writer.
4. Wire `src/kalshi_edge/bootstrap/live.py` after `persist_prediction_tick` returned a saved final; research failures logged without impacting live forecasts or trading.
5. Add docs with PowerShell instructions and non-claim of accuracy gain.
6. Require full current-head GitHub CI bootstrap tests, full tests, production container build green; review diff; merge only then.
