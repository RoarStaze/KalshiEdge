# TimesFM-3 31k-Market Settlement Adapter — Research-Only Design

**Date:** 2026-11-14

## Intent
Use the up to ~31,000 historical KXBTC15M markets already collected on the user's laptop to train a supervised **settlement-probability adapter** on top of TimesFM-3's frozen, price-only zero-shot outputs. Compare against frozen TimesFM-3 directional decisions on a later, chronologically separated development-only evaluation cohort. Never replace the live model or consume the promotion lockbox.

## Verified constraints and considered approaches
- TimesFM-3 downloaded weights have Google's non-commercial/non-production license. Outputs and derivatives cannot be used in production or for commercial trading. This experiment must stay research-only, with a mandatory explicit license acknowledgement on inference and no Trade/AutoTrade integration. Google Cloud services have separate terms.
- Official Google LoRA tutorial is for **TimesFM-2.5**, not TimesFM-3. Do not represent adapter training as fine-tuning all TimesFM-3 weights.
- Existing scripts `timesfm3_quickcheck.py` and `timesfm3_walkforward.py` implement verified 5-second Binance context and 480-second evaluation on 768 markets; reuse those functions, not duplicate data-source definitions.
- Full parameter fine-tuning: unverified for TimesFM-3 on RTX 4050 6 GB; reject for this task.
- Reuse existing 768 historical walkforward: inadequate for user's 31k training request.
- **Selected:** large-development-cohort supervised adapter, strict chronological splits, isolated trained adapter artifact under ignored research-only directory.

## Output
- Preparation from signed, versioned verified `features.parquet` and official settlement manifests; exact 480s checkpoint, Binance 5s context ending 5s before checkpoint. Use development groups only and refuse duplicate/mismatched identities. Source dataset SHA and record SHA bind all later stages.
- Frozen TimesFM-3 inference of terminal BTC price, separate research venv and explicit noncommercial license acknowledgement; resume completed rows after interruption with integrity checks. No model-weight updates.
- Train a small regularized probabilistic supervised adapter on features derived only from causal 480s observations and TimesFM-3's own pre-settlement terminal price prediction. Time-ordered fit/calibration/final-evaluation with 2-market embargoes; no hyperparameter search on holdout. Save only research report and research adapter coefficients.
- Compare against TimesFM-3's unadapted point direction, plus simple last-price persistence. Calibrated probability Brier/log-loss, 69%-confidence coverage and qualified accuracy for adapter; no claims of executable P&L or comparison to deployed Model #1 weights.
- Official Google links: https://github.com/google-research/timesfm ; https://huggingface.co/google/timesfm-3.0-pytorch ; https://github.com/google-research/timesfm/tree/master/timesfm-forecasting/examples/finetuning

## Limits
- Prior 768-market experiment was already inspected; this development-only evaluation is exploratory, not sealed prospective test or promotion evidence.
- Binance BTCUSDT differs from Kalshi BRTI/official index. The point-price terminal forecast is not a forecast of the final reference-window average.
- The user's historical market files/RTX4050 are not mounted in GitHub CI, so actual 31k training/inference cannot be claimed here.
