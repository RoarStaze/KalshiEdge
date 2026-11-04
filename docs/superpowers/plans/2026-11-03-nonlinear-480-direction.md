# Eight-minute nonlinear outcome challenger — implementation plan

## Evidence and objective

The OOF correctness model gave ROC-AUC 0.6323 over 2,253 skipped 480s development cases. The first 20 ranked skips yielded 19 correct but 250 yielded only 207. No selected rescue rule met a development precision guard. Higher-confidence rescue thresholds alone do not reliably improve BOTH conditional accuracy and coverage.

Build ONE fixed nonlinear **direction prediction model** rather than tuning another threshold on the same outcomes. This is research only. Do not claim independent validation or modify live V5, the frozen specialist, AutoTrade, or Kalshi orders.

## Predeclared modeling protocol

- Input: provenance and SHA-verified prelockbox development rows only, using `specialist_480.select_eight_minute_rows` with its strict source timestamps, two-sided quote, true 480-second checkpoint and duplicate exclusion.
- Baseline: existing six-feature standardized logistic (C=0.15, lbfgs, random_state=0). Nonlinear challenger: sklearn HistGradientBoostingClassifier with **the same six features**: learning_rate=.05, max_iter=140, max_leaf_nodes=9, min_samples_leaf=80, l2_regularization=5, max_bins=128, early_stopping=False, random_state=0. No parameter search.
- Development chronological split: first 60% training, next 20% screening, last 20% confirmation; require full 900-second market gap for all transitions; reject overlapping tickers/groups, duplicated timestamp groups and single-label training.
- Both models fit **once**, using only first 60% development labels. Both models score the next two blocks, without refitting or using block-2 labels to set hyperparameters. Gate confidence remains fixed to `2*abs(p_yes-0.5)>=.69` for both.
- Each block includes same-market fixed-gate `qualified`, `correct`, `incorrect`, `skipped`, `coverage`, `accuracy`, and `correct_per_market`, plus Brier/logloss. A joint pass requires strictly greater coverage **and** conditional accuracy **and** correct-per-market by candidate on **each block**, >=100 qualifying candidate forecasts, and non-null baseline accuracy.
- Only if BOTH development blocks pass, compare candidate and baseline on the later historical prelockbox calibration partition, still without refitting or tuning. Calibration outcomes have been previously inspected and cannot establish independent performance; not used to choose a model. No reserve lockbox access or automatic promotion.
- If either block fails, report failure with full development metrics and refuse evaluation on calibration labels.
- No artifact writes, weights, order submission, or changes to live confidence policy. All results JSON and self-described.

## Development steps

1. Add `tests/bootstrap/test_nonlinear_direction_480.py` first, covering paired metrics, causal timestamps, chronological partitions and group overlap, fail-closed, no model promotion, and label independence of earlier model predictions. Confirm RED in GitHub CI because module is missing.
2. Add `src/kalshi_edge/bootstrap/nonlinear_direction_480.py` CLI and pure evaluator.
3. Add `docs/NONLINEAR_480_DIRECTION_CHALLENGER_2026-11-03.md` with PowerShell report and interpretation.
4. Verify current SHA's full Bootstrap tests, entire pytest suite, and production container build in CI; inspect diff; merge only after green.

## Limitations

Historical positive outcomes are descriptive, not a proven deployed signal or profitable trades. Future timestamp-aligned official-settlement prospective validation will still be required for any new model. Existing V5 480s model is NOT the logistic baseline used in this research comparison.
