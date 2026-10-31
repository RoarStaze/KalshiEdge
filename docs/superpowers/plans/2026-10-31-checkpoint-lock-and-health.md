# Checkpoint-first persistence and read-only checkpoint health

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Tests precede code; merge only after complete CI.

**Goal:** Reduce avoidable forecast-lock latency introduced by lower-priority 1Hz prediction log writes, and expose measurable missed-checkpoint causes without modifying trading or the deployed model.

**Architecture:** Keep the current final gate, immutable market-scoped writer, and 2-second fail-closed cutoff. In `bootstrap.live`, add `persist_prediction_tick` which gates and saves the final forecast before appending the continuous JSONL record, then call it from the existing predictor loop. Add a separate read-only `bootstrap.checkpoint_health` CLI module that scans immutable final forecasts and reports reason codes, lateness and quote coverage.

**Tech stack:** Python 3.11+, Pydantic, pytest, GitHub Actions.

**Spec:** The prior live loop executed `writer.append(prediction)` including `fsync` before `final_gate.evaluate(...)`; avoid blocking the time-sensitive lock with unrelated continuous disk IO. Do not backdate or relax `MAX_CHECKPOINT_LATENESS_NS`.

## Constraints
- Do not touch market execution, API credentials, arming, thresholds, stake size or models.
- Preserve immutable final forecast deduplication and original forecast timestamp semantics.
- Separate partial evidence: code ordering is a possible contributor, NOT a proven cause of the user's one missed market.
- Keep the local Windows program running; GitHub CI and read-only analytics do not remotely stop it.

## Test plan
- [ ] Test exact event order `gate -> final save -> continuous append`.
- [ ] Test persistent final save survives a failure in lower-priority continuous append.
- [ ] Test that live inference loop uses the helper.
- [ ] Test checkpoint health with successful locks, late, low-confidence, and feed-unhealthy skips.
- [ ] Test duplicate market exclusion, recent market ordering, empty/invalid windows, corrupted input JSON.
- [ ] Execute complete CI and inspect failures before merging.
- [ ] Verify merged `main` contains both changes and provide explicit local restart procedure if the user chooses to reload.

## Observability

Run `python -m kalshi_edge.bootstrap.checkpoint_health` from the repository
after syncing. It reads only `data/bootstrap/final_forecasts` and emits JSON;
it does not require Kalshi authentication, train anything or submit orders.

**Research:** Poor evaluation hygiene can inflate confidence in trading models;
preserve prospective lockbox and real fill/fee economics. See
Bailey et al. (2017), *The Probability of Backtest Overfitting*,
https://escholarship.org/uc/item/4w1110bb

Do not promote a model or increase live stakes based on the new report.
