# TimesFM3 vs KalshiEdge full-pipeline historical walk-forward backtest

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Never claim empirical gains without running on the local dataset.

**Goal:** Construct a reproducible, market-disjoint, chronological evaluation of a freshly refitted full KalshiEdge prediction pipeline and zero-shot TimesFM3 on exactly matched historical KXBTC15M markets at 480 seconds to settlement.

**Architecture:** Use the existing verified baseline dataset and FULL model training pipeline (structural Monte Carlo, historical classifier, calibrated stacker, residual, ablations). For each of 3 expanding folds, use earlier development markets for fitting, separate later calibration markets, embargo one market, and evaluate 256 subsequent unseen groups. Restrict all train/cal/test rows to the ORIGINAL current development window, not the reserved 1,222-market lockbox, and create zero persistent promoted model files. TimesFM3 runs isolated in the already installed separate research venv with the original no-commercial-use license gate. Scores use matched market outcomes and date-block paired bootstrap.

**Important distinctions:**
- The model compared is a **refit of the actual KalshiEdge training pipeline using earlier markets**, not the current promoted Model #1's fitted weights. The 32-market biased comparison is NOT reused.
- TimesFM3 pretrained training corpus's complete BTC overlap is not known. Historical testing is out-of-sample for the refit KalshiEdge model; TimesFM3 is fixed zero-shot.
- This is NON_DEPLOYABLE research only. TimesFM3 model card says weights are non-commercial; do not use outputs to trade without separate rights.
- Relative accuracy and performance cannot be measured in GitHub CI without the user's private data and installed checkpoint. Report that explicitly.

**Files:**
- `src/kalshi_edge/bootstrap/training.py`: opt-in research-only context validation for full fit; production call untouched.
- `scripts/research/timesfm3_walkforward.py`: deterministic fold planning, prepare + infer + score CLI; hash, integrity, date-block CI.
- `tests/bootstrap/test_timesfm3_walkforward.py`: chronology, embargo, lockbox exclusion, score arithmetic, no overclaim.
- `tests/bootstrap/test_training_research_context.py`: reject partition override that touches newer calibration or locked markets.
- `docs/TimesFM3_WALKFORWARD.md`: exact Windows commands and limitation documentation.

## Implementation tasks
- [ ] Write failing tests for folds, scope, alignment, and context override safety; run red CI and inspect expected failures
- [ ] Add opt-in guarded research partition support to full training pipeline; normal production training unchanged
- [ ] Add historical refitting and date-block comparison script using exact 480s causal verified inputs; no model promotion
- [ ] Add documented research-only install/run commands, optional persistent fold-state safeguards as supported
- [ ] Run complete CI, inspect logs and PR diff, merge to main only when green
- [ ] Present Windows commands to run private 768-market benchmark; request measured result for interpretation
