# Implement continued fitting of active predictor

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development, superpowers:verification-before-completion.

**Goal:** Train from currently active saved classifier weights on new 480-second settled markets, skipping fourfold baseline rebuild and research-only fallback.

## Plan
- [ ] RED: unit tests for logistic/HGB/XGB continued weights, source model immutability, rejects incomplete/overlapping/new-data or changed model identity, no live mutation on failed evaluation, preservation of v2/v3 pointer.
- [ ] GREEN: pure `bootstrap/model_continuation.py` classifier continuation, hash-backed experiment/provenance, paired fresh holdout 63%-gate evaluation and v3 promotion.
- [ ] RED/GREEN: new `CONTINUE_MODE` in controller with readiness and worker state; main Train Now routes to continuation (if an active model exists) and never silently falls back to research or full retrain; historical training modes remain for initial bootstrap/explicit CLI.
- [ ] UI active-model continuation description, eligible count and `TRAINED_NOT_PROMOTED` informative result; previous results still accurately labelled.
- [ ] Full GitHub CI bootstrap, suite, Docker and independent PR CI; merge to main only on success. User PowerShell update; no local trading/control.
