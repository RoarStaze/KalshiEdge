# Eight-minute specialist training from verified point-in-time dataset

**Goal:** Reduce unnecessary confidence SKIPs by learning a genuinely better probability model for the **480-second** final checkpoint. Keep the deployed model and live AutoTrade unchanged unless later independent data supports a replacement.

## Design

Unlike earlier PRs #72–73, which re-scored 99 repeatedly inspected live replay observations, this experiment trains from the **verified `derived/features.parquet`** of settled KXBTC15M markets. It reuses the repo's manifest, provenance and split verification via `training._verified_context(root)`, and **reads only development + calibration rows before the reserved lockbox** via `training._read_rows_before_lockbox(context)`. It never reads the lockbox labels or modifies the trained production bundle.

Select exactly one genuine 480 ±1-second row per ticker. Require complete, causal source timestamps, fresh BTC and completed Kalshi candle sources, positive realized volatility, usable 60s momentum, and an actual valid two-sided quote; reject wrong-horizon, stale, invalid, or duplicate rows rather than impute their values.

Predeclared five features: `btc_normalized_distance`, `btc_realized_vol_60s`, `btc_realized_vol_300s`, `btc_return_60s`, `kalshi_mid`, and `kalshi_spread` [six features]. Fit StandardScaler and regularized LogisticRegression(C=0.15) on original **development groups only**, and score **later calibration groups only**. No tuning with their labels.

Only run if >=80 distinct development 480s markets, >=20 later calibration markets, both train outcomes present, disjoint markets, and strict chronology. Report the candidate's proper scores and the same-market Kalshi quote midpoint, accuracy, 69%-confidence coverage and qualifying hit rate, and missing/invalid counts. Output **no serialized model**. The calibration period is not an untouched external lockbox and cannot prove out-of-sample production skill.

## Tasks

1. Commit tests first for causal source/feature/quote checks, correct 480 selection, no 240 contamination, duplicate exclusion, minimum market counts, chronological separation, and hypothetical confidence coverage.
2. Verify red CI indicates absent implementation, not faulty tests.
3. Implement a minimal train/evaluation library using frozen sklearn hyperparameters. Avoid saving models, order changes, or calibrating confidence output in live code.
4. Expose `python -m kalshi_edge.bootstrap.specialist_480` reading verified local Parquet development/calibration only.
5. Run green GitHub Actions full suite and prod build; review output and merge.

## Critical limits

- The original reported 74% all-market directional accuracy and 90% accuracy of 10 above-gate forecasts on a 39-market replay is NOT independent validation. Do not claim an accuracy or profitability win based on this older sample.
- Using later *development calibration* records is a research diagnostic, not a new independent lockbox. Final promotion must use a never-before-evaluated future test period and actual fee/ask/fill economics.
- Kalshi's crypto market settlement follows exact contract terms, often a CF Benchmarks index averaged over a named minute, not Binance spot at arbitrary seconds: https://help.kalshi.com/en/articles/13823838-crypto-markets
- Proper Brier/log loss reward calibration and discrimination, not extremity: https://scikit-learn.org/stable/modules/calibration.html
- Chronological train/test separation avoids future-label leakage: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
