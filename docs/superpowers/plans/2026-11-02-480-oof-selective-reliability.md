# OOF reliability-gated 480-second forecast — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Test-first red / green and CI evidence required.

**Goal:** Research a fixed prediction-reliability gate that can both issue more correct KXBTC15M 8-minute directions and improve accuracy of those issued, without fitting on the scorecard labels.

**Architecture:** Use the existing provenance-verified 480s development/calibration split. An ordinary six-feature logistic specialist trains direction on early development rows. A separate regularized binary correctness model trains on strictly later, expanding-window out-of-fold development predictions, using causal features and no evaluation labels. Train the final direction on development and score both original fixed 69% gate and fixed 85% correctness-probability gate on *later calibration* markets, plus a rescue-only policy. Never write weights or affect deployed models.

**Tech Stack:** Python 3.12, scikit-learn (research extras), existing FeatureRow/specialist integrity checks, pytest.

**Spec:** This file.

## Non-negotiable constraints

- Predeclare direction logistic C=0.15 and reliability logistic C=0.10, StandardScaler, random_state=0, no parameter grid or threshold tuning.
- Predeclare 0.69 original model confidence; 0.85 model-estimated probability that predicted direction is correct.
- Feature names (correctness model): directional confidence, market implied directional confidence, direction agrees with market, absolute normalized BTC-to-strike distance, log BTC 60s/300s volatility, absolute 60s return, Kalshi spread and quote staleness. **Only features available at the recorded checkpoint**.
- Causal strict order: minimum 80 development markets before first OOF target; divide remaining observations into 4 chronological folds, refit direction on earlier data only, meta fit on OOF predictions; final scoring only on later calibration rows, with one complete 15-minute market embargo.
- Deduplicate markets and group IDs; reject noncausal/invalid evidence and overlapping or reversed train/evaluation chronology.
- Score same eligible evaluation rows; add counts correct / incorrect / skipped, coverage, conditional accuracy, correct-per-market, uncertainty, and comparison. No winner selection or automatic promotion.
- No forecast, AutoTrade, data collection, settlement, live scoring, model freezing, or API changes.

## Testing checklist
- [ ] Write failing tests for time order, fixed thresholds, correct-vs-incorrect gate metrics, missing/OFF 240s evidence, dual-class reliability labels, OOF chronology, and no side effects.
- [ ] Confirm expected CI failure because new module is missing.
- [ ] Implement offline `src/kalshi_edge/bootstrap/selective_reliability_480.py` plus one command-line runner.
- [ ] Run repository GitHub Actions full pytest and container-build CI; review diff and results.
- [ ] Document limits: retrospective calibration is inspected, not independent validation; baseline is fixed historical 480s logistic, not deployed v5; any plausible improvement must be frozen and separately validated on future settlements.
- [ ] Merge only if PR CI verified green.
