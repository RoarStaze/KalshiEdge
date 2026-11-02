# Read-only 480s strike/BRTI/market challenger (TDD)

**Goal:** Improve the quality and coverage of actionable 8-minute forecasts without inflating confidence scores or relaxing live AutoTrade. Test one *prespecified*, small-sample-regularized challenger against the incumbent and same-checkpoint market odds; only report historical results.

**Data:** Reuse the strict, first-tick causal replay of older settled markets (no retroactive final locks). Extend only opt-in research rows to contain the existing archived BRTI, strike, and BRTI source timestamp from that same recorded tick. Do NOT use subsequent price data, new Kalshi requests, or a market's settlement as a feature.

**Features:** [logit(existing model P(YES)), logit(Kalshi bid-ask midpoint P(YES)), clipped signed BRTI minus strike in units of 100 bps]. No hyperparameter search. Use sklearn StandardScaler fitted ONLY on the first 60 records followed by LogisticRegression(C=0.15, solver=lbfgs, random_state=0), then score all later >=20. The distinct 480s model is an offline challenger, not promoted model. Require one homogeneous signed bundle hash and actual complete same-tick market quote, source-fresh BRTI (no older than 5 seconds), valid strike, valid probabilities and verified outcome. No leakage or future labels may enter input features.

**Validation:** Proper Brier and log loss, accuracy, 69%-confidence qualifying coverage, and directional hit rate in qualifying group for *both models on same later markets*. Report criterion **joint_target_met_retrospectively** true only if candidate has lower Brier, lower log loss, higher overall accuracy, more qualifying forecasts and higher qualifying accuracy. Even when true, no deployment without independent future tests and executable fee-inclusive economics. Repeated experiments on previously inspected historical sample are not prospective model evidence.

**Tasks:**
1. Add failing synthetic tests for causal replay feature transport, 60/40 chronological split, joint-target positive scenario, insufficient/missing/stale/invalid inputs, mixed hash, duplicates and single-outcome-class refusal.
2. Verify red CI before implementation.
3. Add data fields only to opt-in read-only replay rows, then implement narrow challenger and CLI. No production prediction or trading path changes.
4. Full GitHub Actions CI (bootstrap/full/container), inspect actual test outputs and failure logs; only merge if green.
5. User can run research CLI in another PowerShell window with local KalshiEdge still running. Never hot-reload active AutoTrade or silently promote candidate.

**Caveat:** Earlier 99-record replay is retrospective, may be correlated, and scored multiple times. This is hypothesis generation, NOT proof that 8m model accuracy, confidence, or trading profitability increased. Predictions that cross the confidence gate are not guaranteed fills. Market bid/ask midpoint is not an executable YES/NO ask.

**Primary sources:**
- scikit-learn TimeSeriesSplit time ordering/gap: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- Kalshi orderbook execution/spread: https://help.kalshi.com/en/articles/13823828-the-orderbook
