# Robust Chronological Hyperparameter Selection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and verification-before-completion before merging.

**Goal:** Eliminate high-variance tuning on one or two BTC markets and redundant feature-matrix builds, without touching sealed final lockbox, live model, promotion thresholds or AutoTrade.

**Architecture:** Expand only the inner hyperparameter tuning slice within each already-permitted development training fold. Use a bounded trailing chronological 20% of training groups, capped at 512 and retaining at least two training groups. Score the inner validation at the user’s real 480s forecast horizon when complete 480s examples are available; for older one-checkpoint generic training datasets with no 480s, preserve the general all-horizon fallback. Cache the precomputed feature matrix and labels for the single candidate training pass so each inner grid search avoids rematerialization. The score remains proper log loss, Brier tie-break; no new hyperparameter candidates are introduced.

**Tech Stack:** Python, NumPy/scikit-learn/XGBoost existing library, existing `FeatureRow` and `probability_metrics`.

**Spec:** User-selected Prompt Perfect rewrite asks to improve training accuracy and coverage using 31,157 historical markets / 560,826 checkpoint rows without overfitting 1,222 untouched fresh lockbox markets.

## Global Constraints
- No reading, tuning, or retraining against untouched final lockbox.
- No changes to confidence gate, model serialization, research feature schema, deployed inference, calibration, or trading.
- Real measurements of accuracy changes require actual local historical dataset; CI synthetic tests cannot prove improvements.
- Avoid enormous additional search and maintain deterministic seeding / chronological splits.

## Review Focus
- No market overlap between inner fitting and inner validation.
- At least two inner fitting markets for tiny datasets; capped sample for huge ones.
- 480s-only tuning scores when checkpoint exists, with deterministic fallback for non-480-only test data.
- Exactly one matrix construction per train_candidate_models call, not per inner grid trial.
- All existing training/report/promotion tests and production build succeed.

## Tasks
- [ ] Add failing tests for bounded inner validation size and no inner overlap.
- [ ] Add failing tests that tuning at exact 480s ignores misleading later-checkpoint labels.
- [ ] Add failing test that entire candidate pass constructs one feature matrix.
- [ ] Implement small isolated changes in `src/kalshi_edge/bootstrap/models.py`.
- [ ] Run complete CI and review diff, then merge safe PR to main.
