# WeatherEdge probabilistic station forecasting implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Use test-first, fail/green evidence, and full CI before merging.

**Goal:** Deliver an actual point-in-time temperature probability forecaster, historical training/holdout workflow, and read-only WeatherEdge dashboard integrated into KalshiEdge.

**Architecture:** Reuse the WeatherEdge v0.2 SQLite `samples(station,source,event_time,received_at,payload)` ingestion contract, avoiding unsafe pickle imports and Bitcoin trading code. A standalone standard-library weather probability engine creates labeled *research* forecasts from fresh METAR+HRRR+NBM; trained station-specific bias/sigma metadata live in JSON. KalshiEdge routes expose forecasts read-only, and CLI performs strictly chronological training.

**Tech Stack:** Python >=3.12, sqlite3, JSON, math, datetime, pathlib, httpx, pytest; optional external WeatherEdge collector for GRIB ingestion.

**Spec:** WeatherEdge market workspace in `docs/WEATHEREDGE_MARKET.md`; user's weather system specification in conversation.

## Global constraints
- No live weather order routes; no credential leaks or model pickle loading.
- Missing, late, malformed and stale data must fail closed.
- Never call research forecast probabilities calibrated or contract-specific without evidence.
- No fake weather data or settlement labels.
- Never use observation time as availability time.
- No repo/main changes until CI passes; branch + PR.
- Output actual tests, not presumed results.

## Review focus
1. Late-arriving METAR must not alter past snapshots.
2. Mixed NWP cycles must never form one daily maximum.
3. Latest *incomplete* NWP cycle must not corrupt an older complete cycle.
4. Missing or invalid model artifacts must fail closed.
5. UI/HTTP routes must never submit real weather trades.

## Tasks
- [ ] **1. Tests and base models.** Add `tests/weather/test_predictor.py` for event-time, receipt-time, freshness, cycle integrity, distribution normalization, and fail-closed status; establish RED via CI. Implement `src/kalshi_edge/weather/predictor.py`; rerun tests.
- [ ] **2. Chronological fit and scoring.** Add tests for unique settlement days, holdout segregation, baseline comparisons, immutable JSON model artifacts; implement `src/kalshi_edge/weather/training.py`; rerun tests.
- [ ] **3. Read-only integration.** Add GET `/api/weather/forecast`, status and Weather forecast UI with station selection, probabilities and provenance. Test preview/live routing; no POST.
- [ ] **4. CLI and docs.** Add `kalshi-edge weather-forecast` and `kalshi-edge weather-train` with explicit inputs and fail-safe errors, document separate v0.2 collector, Python dependencies, and use.
- [ ] **5. Verification.** Run full GitHub Actions CI (Python tests and container build), review diff, merge PR to main only on green; provide PowerShell update commands.

## Rulings
- Forecasts are **experimental baseline** until independent first-party labels and unmodified forward holdout metrics exist. Cost: cannot claim validated accuracy or positive P&L yet.
- No fake contract price/EV from generic 1-degree weather distributions. Cost: prices and trading are not enabled in this phase.
