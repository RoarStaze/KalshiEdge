# WeatherEdge V6 Live Quote Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display genuinely refreshed, timestamped Kalshi ask prices and visible liquidity for validated hourly Weather Index contracts while keeping the weather forecaster paper-only.

**Architecture:** Revalidate shortlisted listed tickers independently using individual Kalshi Market GET; persist source-audited quotes, exclude stale/mismatched/closed data, expose separate exchange prices and model probabilities in hourly forecast API and UI. An independent supervisor loop refreshes prices without blocking official index/NWP ingestion.

**Tech Stack:** Python, SQLite, httpx, Decimal, pytest, existing WeatherEdge UI.

**Spec:** docs/weather/hourly_live_quotes_v6.md

## Global Constraints
- Official HS index series only. No invented close, strike or quote. No order placements.
- Source-stamped query and 35-second quote freshness; 20-second collector schedule; at most two selected strikes per city.
- Never treat model estimates as Kalshi prices or claims of profits.

## Review Focus
- Expired tickers returning last quotes: show CLOSED without buy quote.
- Market GET mismatches original series/event/threshold: never associate its quote.
- Missing ask depth: show NO_VISIBLE_ASK rather than 0¢.
- Source timeout: keep baseline prediction and show QUOTE_UNAVAILABLE or stale quote.
- Old cached quote: never make it appear live; preserve exact local timestamp.

---

### Task 1: Strict quote parsing/storage with red-green tests
**Files:** Create src/kalshi_edge/weather/hourly_quotes.py; Create tests/weather/test_hourly_quotes.py
- [ ] Write failing tests for quote identity/decimal/size/close correctness.
- [ ] Verify RED against current branch.
- [ ] Implement parser and SQLite evidence with freshness-based read.
- [ ] Verify GREEN with test suite.

### Task 2: Bounded independent exact-ticker collector
**Files:** hourly_quotes.py; tests/weather/test_hourly_quotes.py
- [ ] Add failing transport tests for exact GET, bounded shortlist, timeout isolation.
- [ ] Implement 2 per city at 20-second polling; first-boot and no-market safety.
- [ ] Verify GREEN.

### Task 3: Surface prices in forecast API and plain-English UI
**Files:** hourly.py, hourly_ui.py; tests/weather/test_hourly_quotes.py
- [ ] Add failing tests for missing/stale/closed quote propagation, no simulated advice.
- [ ] Overlay quote data with exact city/event/strike checks; render separate model and price information.
- [ ] Verify GREEN.

### Task 4: Supervisor + full CI and review
**Files:** runtime.py, tests/weather/test_hourly_quotes.py
- [ ] Write failing supervisor-visibility checks.
- [ ] Add isolated polling and clear statuses.
- [ ] Run full test suite and container. Review PR diff, merge to main only after green.
