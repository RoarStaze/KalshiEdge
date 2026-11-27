# WeatherEdge V8 — regime-aware hourly uncertainty

## Intent
Improve the reliability of next-hour temperature threshold probabilities when short-term Kalshi Index trend estimates disagree. Keep the current point predictor unchanged unless the existing chronological candidate-selection gate establishes improvement. Model **uncertainty**, not fictitious forecast certainty or inferred trading profit.

## Alternatives and chosen approach
- Selected: train-only bounded, regularized uncertainty inflation as a function of fast/slow Index slope disagreement; compete with fixed-sigma models on Gaussian CRPS, then require the existing 24-hour chronological holdout to beat incumbent MAE and CRPS without damaging 90% interval coverage.
- Not selected: heuristic extra error added regardless of evidence, or unvalidated neural networks.
- Not selected: mixing airport METAR and Weather Index readings, which have different target definitions.

## Behavior
- Use only first-stored Kalshi Index observations available at decision time; same city, configuration and lead bucket. Never use the holdout to fit gains.
- Compute disagreement D = min(1, abs(fast_slope - slow_slope) / 3) with slopes measured in °F/hour.
- For sufficiently diverse training examples (at least 80 cases, with 16 quiet and 16 high-disagreement), fit an extra nonnegative multiplier 1 + gain*D for the Gaussian temperature sigma. Bound gain to [0, 1.25], resulting sigma to a sensible finite range, and keep mean forecast identical to the existing robust-adaptive regression.
- Select among existing static models, existing adaptive regression, and this candidate by **training** CRPS only. The untouched last 24 completed hours decide whether a selected candidate may be used. A V8 candidate must additionally reduce CRPS by at least 0.01 compared with the prior V7 training champion on those same 24 hours, without losing more than 15 percentage points of nominal 90% interval coverage; otherwise fall back to V7 and its own original validation decision.
- Version the active model distinctly from V7 so old physical-model promotion locks cannot be reused.
- Keep prior fallback behavior if evidence is insufficient, malformed or candidate loses; daily-weather collection and order placement remain disabled.

## Evidence boundary
Synthetic tests establish implementation and leakage safety only. Real temperature skill, interval calibration, per-threshold Brier/log loss and actual trading edge remain unverified until enough genuinely frozen future market outcomes are collected. Run full GitHub CI and container build before merging.
