# WeatherEdge V8 Regime Uncertainty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task; write failing tests first.

**Goal:** Improve probabilistic forecasts for hourly Kalshi Weather Index thresholds under divergent short-horizon trends.
**Architecture:** A pure train-only regime-sigma module extends the existing V7 adaptive mean. The nowcaster adds one competitive candidate. The hourly forecaster assigns the new model a distinct version only after chronological gates pass.
**Tech Stack:** Python 3.13, SQLite, pytest, GitHub Actions.
**Spec:** `docs/weather/hourly_regime_uncertainty_v8.md`

## Global constraints
- Hourly Weather Index only; no airport daily highs or substitute labels.
- Train-only fit; same city/index-config/lead; guard actual local receipts.
- No orders, auto trading, secrets or changes to BTC/Gold.
- No performance claims without genuine prospective evidence.

## Review focus
- Invalid/nonfinite coefficient and live input must fail closed.
- Insufficient quiet/volatile sample coverage must preserve V7 fallback.
- Holdout result modification must not influence fitted gain.
- Sigma must increase with disagreement while point forecast is unchanged.
- V8 model kind must not inherit physical V4 locks validated against V7.

## Task 1 — regime-sigma module
Files: create `src/kalshi_edge/weather/hourly_volatility.py`; create `tests/weather/test_hourly_volatility.py`.
- [ ] Add tests for deterministic train-only selection, bounded gain, invalid values and no diverse training.
- [ ] Observe failure on missing module through CI.
- [ ] Fit gain/scale using older rows only; minimize training Gaussian CRPS.
- [ ] Run focused tests.

## Task 2 — nowcaster integration
Files: modify `src/kalshi_edge/weather/hourly_nowcast.py`, extend `tests/weather/test_hourly_volatility.py`.
- [ ] Test candidate choice on a held-out series with heteroscedastic residuals.
- [ ] Show holdout labels cannot fit the gain and degradation blocks activation.
- [ ] Score per-case volatility sigma in Gaussian CRPS and interval coverage; choose from existing and new models using training scores.
- [ ] Run focused and full weather tests.

## Task 3 — versioning and release
Files: modify `src/kalshi_edge/weather/hourly.py`, extend test file.
- [ ] Add failing _forecast model_kind regression test.
- [ ] Version a selected V8 model distinctly without enabling real orders.
- [ ] Run complete CI, package compile, and production container build.
- [ ] Review diff, open PR and merge to main only with green checks.
