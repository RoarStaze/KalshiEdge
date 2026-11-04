# Precision-guarded 480s correctness rescue — 2026-11-03

## Context and objective

The already-inspected 2,900-market calibration comparison showed:
- original 69%-direction-gated logistic: 863/949 correct, 90.94% conditional accuracy, coverage 32.72%
- meta-correctness 85%-threshold union: 967/1079 correct, 89.62% conditional accuracy, coverage 37.21%
- 130 new rescue predictions: 104 correct, 26 wrong, 80.0% accuracy.

**Objective:** evaluate whether a development-only precision guard can reject weak additional predictions while increasing qualifying correct forecasts and conditional accuracy. This is not permission to tune on those 2,900 calibration outcomes again.

## Fixed experiment

1. Load verified 480s development and later calibration via existing manifest hash/provenance. Never load reserved lockbox; reject all duplicate, stale, invalid and noncausal rows.
2. Use existing fixed six-feature logistic (C=0.15). For the second half of chronological development, generate four expanding-window, strictly earlier-fitted OOF direction predictions separated by a full 900-second market window.
3. Train fixed correctness logistic C=0.10 on the **first two OOF blocks only** (~6,488 cases). Its labels encode *whether the OOF direction was correct*, not actual direction. Use exactly the existing nine timestamp-causal reliability features.
4. Choose ONE threshold from the predeclared grid `[.875,.90,.925,.95,.975]` using **third OOF block only**. A candidate must add >=40 otherwise skipped forecasts, its observed rescue accuracy must exceed that block's original gate accuracy, and its rescue Wilson 95% lower bound must exceed that original gate accuracy. Choose candidate maximizing extra correct forecasts; tie break higher threshold.
5. Test that frozen threshold on **fourth OOF block**, not used to train meta or select threshold. Require >=20 added forecasts, rescue accuracy strictly exceeds original 69%-gate qualified accuracy, and union accuracy strictly improves with greater coverage. If not, refuse evaluation/promotion.
6. Only after internal confirmation passes, fit direction model on entire verified development set (no calibration labels) and score baseline 69%, guarded rescue union, plus rescue-only on the **later 2,900 calibration** markets. Counts of additional incorrect forecasts and absolute correct-per-market are mandatory.
7. Never save model, promote weights, relax live thresholds, alter AutoTrade, or use market settlement for individual signal selection. All output read-only JSON.
8. This development-only threshold is not a deployable policy. Previously inspected calibration is research validation and not an independent future cohort. Even successful results require freezing and new post-registration forward outcomes.

## Files and steps

- `tests/bootstrap/test_precision_guarded_480.py`: RED first — threshold selection, noise rejection, strict time/duplicate controls, no mutations, no evaluation-label threshold tuning, full report.
- `src/kalshi_edge/bootstrap/precision_guarded_480.py`: implement fixed split, train and guarded report, CLI.
- `docs/PRECISION_GUARDED_480_RESCUE_2026-11-03.md`: Windows command and interpretation.

## Acceptance

- Red CI fails **specifically for missing module**, then final CI has full pytest and container build green on current PR HEAD.
- Counterfactual rescue is never counted as an actual trade. Reports explicitly refuse to promote.
