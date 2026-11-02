# Future volatility promotion must evaluate eight-minute final checkpoint

**Goal:** Prevent a new volatility-enhanced model from being promoted solely for 240-second performance when the live final lock and AutoTrade decision are at 480 seconds.

**Scope:** Backward compatible with already-promoted models and historical evaluation artifacts. Applies ONLY to NEW volatility challenger evaluations, never retroactively invalidates the existing promoted bundle. Does NOT change live inference confidence, live AutoTrade orders, stakes, calibration, or minimum confidence thresholds.

## Steps (TDD)
1. Add failing isolated tests proving selected 480s per-market lockbox rows, min 80 distinct, and protection against 480s Brier/log-loss/accuracy regression even when overall+240s metrics pass.
2. Verify red suite after fixing any test import/fixture problems.
3. Add simple horizon-specific row sampler with source/timestamp defensive checks and minimum sample requirement.
4. Keep existing volatility_240 metrics; in the same one-time lockbox evaluation score **candidate, promoted incumbent and Kalshi prior** at exactly 480s on identical distinct markets. If data missing, fail closed rather than substitute old 240s metrics.
5. Extend volatility promotion gate: if 480s metrics exist, candidate must be no-worse on Brier/log-loss and direction accuracy, improve a proper score and avoid excessive ECE degradation relative to both incumbent and Kalshi. Require all three 480s fields for new volatility experiments.
6. Validate full GitHub Actions bootstrap, full suite and production container before merging. Running KalshiEdge remains untouched.

**Rationale:** The earlier two 480s shadow candidates worsened on the later historical 39 markets. No evidence supports switching current live weights. Subsequent retraining needs actual horizon-specific independent lockbox protections.

**Caveat:** This guard is not itself better predictive accuracy or lower SKIP frequency. The benefit is avoiding an unsafe future model promotion based on non-live checkpoint results. The 240s volatility model was originally developed for its own horizon; now requires independent 480s improvement too.

**Future work:** Train dedicated 480s features on larger verified dataset with fresh untouched lockbox, explicitly evaluate 69%-confidence high-quality coverage without changing the gate, and use execution-aware AutoTrade P/L.
