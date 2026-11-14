# Align OOF model comparisons with the eight-minute decision

**Date:** 2026-11-14

## Objective
KalshiEdge already tunes candidate hyperparameters against point-in-time 480-second data, but its candidate-family OOF championship and later-period feature/model ablations still score mixed checkpoints. In a multi-checkpoint historical dataset, these selections can optimize horizons at which AutoTrade does not make its final forecast.

## Constraints
- No change to live inference, market/fill logic, confidence thresholds, lockbox gates, labels, data partitioning or model serialization.
- Use only out-of-fold development predictions and original as-of 480-second snapshots.
- Refuse small 480-second groups for model selection: require at least 60 distinct one-checkpoint-per-market OOF markets before using exact 480-second metrics; otherwise retain legacy all-checkpoint selection.
- Do not use the reserved independent lockbox or adjust the minimum using its results.
- Treat the historical accuracy gain as unmeasured until the user executes training and independent evaluation on their local dataset.

## Alternatives considered
1. **TimesFM-3 / Chronos-2:** interesting time-series foundation challengers, but high dependencies and no matched settled Kalshi evidence. Deferred.
2. **Change live threshold or AutoTrade:** could increase coverage while harming calibration and P&L. Rejected without prospective evidence.
3. **Align research model selection to actual 480s checkpoint (selected):** small change to existing statistical model evaluation, no trading-path impact, tests feasible in CI.

## Changes
- Add a shared helper in `bootstrap/models.py` that selects unique valid 480s OOF positions, only with sufficient market samples; otherwise preserves all-checkpoint fallback.
- Use these positions for candidate OOF proper scoring and best-family choice.
- Use the same positions for the later-third OOF scoring that determines feature and XGBoost ablations in `bootstrap/training.py`; maintain chronological order and matched comparisons.
- Add regression tests with synthetic mixed horizons where the most accurate 480s model differs from the best overall model. Ensure tiny/sparse 480s cohorts retain fallback.

## Validation
1. Add failing regression tests first (red GitHub Actions CI).
2. Implement minimal change on feature branch.
3. Confirm complete GitHub CI test suite and production build.
4. Review diff; merge to `main` only with green CI and no blockers.

## Research basis
- Google Research, TimesFM-3 (Aug 31 2026): broad multivariate benchmark results are not evidence for Kalshi-specific settlement accuracy.
- US Office of Financial Research, *An Open Benchmark for Evaluating Time Series Forecasting Methods across Financial Markets* (Aug 25 2026): fair, domain-specific comparisons are required; no universally superior family.
- Kalshi Help Center *Crypto Markets* (Oct 9 2026): rules prescribe averaged CF Benchmarks values.
- Gneiting and Raftery (2007): proper scoring rules provide principled evaluation of probabilistic forecasts.
