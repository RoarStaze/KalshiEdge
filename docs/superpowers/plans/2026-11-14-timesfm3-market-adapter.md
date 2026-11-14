# TimesFM-3 Settlement Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development; verify CI before merging.

**Goal:** Run a non-deployable, provenance-bound TimesFM-3 settlement adapter experiment across all eligible old development markets, up to the collected 31k.

**Architecture:** New isolated research script with `prepare`, `infer` and `fit` commands. Reuse the existing exact-480s context/manifest validation, fit an opt-in small sklearn adapter on earlier records, calibrate on later records and score only a last development-period cohort, with two embargo markets between. Keep production code and trading code untouched.

**Tech Stack:** Python 3.13; PyArrow and sklearn in existing research environment; TimesFM-3 and PyTorch in isolated `.venv-timesfm3`.

**Spec:** docs/superpowers/specs/2026-11-14-timesfm3-market-adapter.md

## Constraints
- No edits to live model, promotion, training GUI, AutoTrade, stake, keys or trading.
- Development-only verified historical rows; never open current independent lockbox.
- TimesFM-3 self-hosted weights are non-commercial/non-production only.
- Report actual counts and do not assume 31,157 contexts are available.
- Preserve at least two embargo groups per segment boundary.
- Never call the trained head `TimesFM-3 weights fine-tuned`.

## Tasks
- [ ] Add failing tests for scoped development selection, time splits and disjoint embargo, probability construction, prediction input integrity and no license acceptance bypass.
- [ ] Implement research-only preparation from verified historical 480s feature rows and causal Binance inputs.
- [ ] Implement resumable TimesFM-3 terminal-price inference using official API.
- [ ] Implement deterministic trained/calibrated settlement adapter and signed exploratory evaluation; ensure no live model file writes.
- [ ] Add usage and limits documentation.
- [ ] Verify GitHub Actions full suite and container build, review diff, PR and merge only green CI.

## Review focus
- No post-checkpoint features or future train/eval labels appear in inference inputs.
- No calibration/holdout re-use or repeated evaluation for parameter selection.
- Existing development history excluded from KalshiEdge's fresh sealed lockbox.
- Resume refuses malformed or provenance-mismatched outputs, does not silently mix records.
- Report explicitly disclaims license, profitability, or validation as superiority evidence.
