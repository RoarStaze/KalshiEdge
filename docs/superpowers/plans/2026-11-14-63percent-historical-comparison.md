# Parameterize historical confidence comparison

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Use RED → GREEN tests.

**Goal:** Support `--threshold 0.63` for both saved models on the same 480s market sample with original 69% comparison parity.

**Architecture:** Add one validated threshold argument to the existing read-only diagnostic. Keep original reference gate and default 60% keys unchanged. No new lockbox consumers.

**Files:** `tests/bootstrap/test_posthoc_confidence_comparison.py`, `src/kalshi_edge/bootstrap/posthoc_confidence_comparison.py`, `docs/POSTHOC_60_CONFIDENCE_COMPARISON.md`.

## Steps
- [ ] Add tests for 63% counts, original 60% regression, rejected bad thresholds, and CLI propagation; verify new 63% tests RED.
- [ ] Implement minimal threshold forwarding and dynamic output names; verify green.
- [ ] Update PowerShell docs with 63% command, 63% probability cutoffs and post-hoc caveat.
- [ ] Run full bootstrap, project tests and production Docker GitHub CI; open PR; merge only clean/green.
