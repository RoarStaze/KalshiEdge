# Safe CUDA holdout and calibration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Use RED → GREEN tests and full CI gate.

**Goal:** extend verified optional GPU routing into lockbox and calibration, independently enable XGBoost CUDA, retain exact CPU parity/fallback.

**Architecture:** Reuse existing CuPy backend and XGBoost smoke probe, pass runtime-only GPU backend through the fitted pipeline, and select CUDA per distinct structural model in research holdout. No model serialization, decision, or live runtime changes.

**Tech Stack:** Python 3.13, CuPy optional, XGBoost optional, pytest, GitHub CI.

**Spec:** docs/superpowers/specs/2026-11-14-gpu-routing-research-evaluation.md

## Global constraints
- Respect `KALSHI_TRAIN_GPU=auto|on|off`; never silently accept unverified CUDA.
- No model feature or training algorithm changes; exact serial/GPU seeded probability parity.
- No credentials/trades/local installation access.
- Do not alter scores/gates or use lockbox for parameter tuning.
- Keep optional CuPy import lazy, CPU tests runnable without GPU.
- Merge only after all full CI jobs pass.

## Review focus
- XGBoost CUDA selection independent of CuPy installation, true device verified.
- GPU path is used in holdout and calibration **when selected**; zero-weight structural incumbent skips backend.
- Auto failures revert to serial/multicore CPU with no mixed partial predictions.
- GPU and CPU use exact same simulation count and near-strike fallback, no live CUDA import.
- No accidental pickle format/schema changes or opened lockbox before experiment gate.

## Tasks
- [ ] Write failing tests for XGBoost independence, GPU forwarding, GPU/CPU fallback in calibration, selector/reuse, and explicit-on fail-closed policy.
- [ ] Implement minimal XGBoost probe decoupling and backend forwarding.
- [ ] Route GPU through authorized holdout and calibration using existing verified selector.
- [ ] Add device-level reporting and documentation, ensure compatibility with existing mocked tests.
- [ ] Run GitHub Actions full bootstrap, full suite, Docker; review diff, PR, merge green.
