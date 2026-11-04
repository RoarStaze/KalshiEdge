# Paired 480-second logistic vs nonlinear disagreement audit — November 3, 2026

## Motivation and evidence

Original six-feature logistic and fixed nonlinear HGBT classifier were fit
once on the same earliest development rows and evaluated on identical later
markets. On 5,190 screening and 5,191 confirmation markets, HGBT had lower
Brier/log loss but also lower 69%-gated conditional accuracy and higher coverage.
Do **not** claim better live forecasts or select a favorable rescue rule from
either previously-inspected cohort.

Goal: explain *which* market classes account for the discrepancy, without
training again, adjusting confidence, or accessing later calibration or reserved
lockbox outcomes.

## Predeclared analysis

1. Add one pure scorer `paired_disagreement_480(labels, logistic_p_yes, nonlinear_p_yes)`
   with fixed 69% gate and strict finite [0,1], binary label, equal-length
   validation.
2. Partition **every** market once, without overlap, into:
   - `both_qualified_agree`
   - `both_qualified_disagree`
   - `logistic_only`
   - `nonlinear_only`
   - `neither_qualified`
3. For every category: count, each model's *issued* correct and wrong totals,
   and number of matching preferred directions. Never call an unissued SKIP
   a successful forecast; do not infer executability or net profits.
4. Aggregate accuracy/coverage and exact net change decomposition from:
   candidate-only correct/incorrect, logistic-only correct/incorrect,
   and both-qualified disagreements (one model correct and the other wrong).
   Show optional fixed `logistic_preferred_union`: accept original qualifying
   logistic forecasts and only add nonlinear-qualified forecast when original
   skips. Never optimize priority or thresholds using labels.
5. Inject the pure scorer into `nonlinear_direction_480._compare` so current
   `development_screen` and `development_confirm` reports include paired
   audit data. Keep the original strict two-development-block rejection:
   if either block rejects, **do not** score historical 2,900-market calibration.
6. README operator PowerShell report for each development period. Clearly label
   descriptive nature and that the comparator is logistic research, not V5.
7. No persisted weights, trades, thresholds, new model search, future outcomes
   or live policy changes.

## TDD and verification
- First add `tests/bootstrap/test_paired_disagreement_480.py`: synthetic
  categories, 0.69 boundaries, both-qualifying disagreement correctness,
  incumbent-preferring union, invalid input, and successful integration into
  rejected-development results. Observe RED on CI missing module.
- Implement `src/kalshi_edge/bootstrap/paired_disagreement_480.py`, then
  integrate with existing `_compare` only.
- Verify current-head full bootstrap/full test CI and production container build.
- Merge only when current PR run is green; the user still needs to run against
  their local verified feature parquet for real results.
