# Verified explicit manual promotion for an accuracy-favored rejected KalshiEdge candidate

**Date:** 2026-11-14

## User need
A full baseline retrain completed a verified one-time fresh 480s holdout of 1,222 markets. Candidate directional accuracy improved +0.90016 percentage points (candidate-only correct 28; incumbent-only correct 17); qualified accuracy improved (349/382 versus 369/405), but eligible coverage declined by 23 and overall probability-quality strict gates did not improve. The user wants to deliberately try the fully trained candidate as Model #N rather than wait for restrictive automatic rules.

## Decision
Do **not** change existing automatic promotion gates or falsify the historical decision. Implement a separate local **manual acceptance** path that converts the *saved, rejected, hash-verified* experiment into a promoted bundle tagged with an explicit manual-override provenance. Only activate on an affirmative local opt-in with exact expected experiment SHA, after previewing immutable candidate/previous model hashes and printed rejection reasons. Preserve last known good pointer for simple rollback. No reevaluation/replay of consumed lockbox.

## Exact safety
- Must be the **current completed baseline** run, evaluation report path under `data/bootstrap/reports/evaluation-<experiment-sha>.json`, source `models/experiments/<sha>.json`, matching report bundle SHA and `VALIDATION_FAILED`; no active training stage.
- Recompute `evaluate.promotion_decision(report)` from saved **metrics only** and require that the original saved decision, including all reason codes, matches. Stop on mismatch/tamper.
- Allow override only if reason set is a nonempty subset of exactly: `INCUMBENT_MODEL_NOT_BEATEN`, `RETRAIN_480_INCUMBENT_NOT_BEATEN`, `RETRAIN_480_COVERAGE_NOT_INCREASED`. All leakage, broken schema, calibration/ECE degraded, training data ambiguity, insufficient lockbox, and required benchmark failures stay hard blocks.
- Require saved, same-market `paired_480_audit`, validating audit against saved aggregate metrics, strictly higher all-market and qualified directional accuracy, same >=80-market cohort, and an existing verified promoted incumbent pointer. No new lockbox accesses.
- The manual bundle retains original experiment hash, report snapshot and **rejection reason codes**, using distinct rule `kalshi-manual-accuracy-override-v1`. The unchanged normal live loader may accept that specific rule with consistency requirements, never pretend the automatic policy passed.
- Explicit local `--expected-experiment-sha` and `--accept-manual-risk` required for activation. CLI `preview` read-only. Backup `default.json` atomically before replacement. Rollback requires exact current promoted SHA, uses original backed-up pointer (verified against promoted bundle and loader checks), and never deletes promoted candidates or reports. No arbitrary report paths or model locations.
- Never submit orders, modify AutoTrade, or activate a candidate through GitHub remote merge; local action only when user chooses. Model becomes loaded only on restart. Manual activation is an experimental trial: user may disable AutoTrade when reviewing behavior.

## Limitations
The observed +0.900pp advantage has a paired 95% bootstrap interval of approximately [-0.164pp, +1.964pp] and p=0.135; no demonstrated forward profit advantage. The strict automatic gate remains preferred by default. Manual policy changes only explicit user-controlled selection, not prospective validation.
