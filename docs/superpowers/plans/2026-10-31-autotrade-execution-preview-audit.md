# AutoTrade execution preview evidence — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion for this plan.

**Goal:** Preserve actual decision-time quote and model-edge diagnostics for each attempted AutoTrade stage, and summarize rejected BUY quotes separately, without changing the real-money order path.

**Architecture:** Migrate `auto_trade_stages` by adding nullable numeric snapshot fields to the existing pre-POST SQLite transaction. Accepted quotes are stored atomically with the immutable order-stage attempt (no extra SQLite commit on the critical path); rejected value-gate quotes are recorded in a best-effort, bounded-per-market/reason aggregate that must not interrupt normal trade execution or skip behavior. A standalone, offline, read-only audit summarizes coverage, rejections and unknowns without claiming real fill prices or profitability.

**Stack:** Python >=3.12, sqlite3, Decimal, pytest, GitHub Actions.

**Source and reason:** Kalshi fill API documents signed order identifiers, count_fp, prices, fee_cost and action/side (https://docs.kalshi.com/api-reference/portfolio/get-fills); fee schedules vary (https://help.kalshi.com/en/articles/13823805-fees). A comparison between preview and verified fills cannot be retrospective if the preview was never archived.

## Steps

- [ ] Add failing regression tests for safe quote snapshot allowlisting, old-database migration, restart persistence, accepted order stage atomic capture, skipped BUY decision aggregation and standalone read-only audit.
- [ ] Verify red CI fails only for missing audit functionality.
- [ ] Add nullable stage quote columns with in-place old-journal migration.
- [ ] Implement quote sanitizer: fixed-point numeric strings and a strict allowlist; no preview IDs, API credentials, account balance or payload dumps.
- [ ] Wire accepted quote metadata into the **same** stage journal insert before exchange POST. Write best-effort rejected signals only after the fee/value gate rejects.
- [ ] Implement a passive audit command on the local journal (no API credentials).
- [ ] Run all existing CI, including real-money order safety tests, and fix regressions before any merge.
- [ ] Merge into `main` only after fresh green CI and inspect main source.
- [ ] Keep the local app running; local binary loads new code only after the user separately updates/restarts at a safe time.

## Review focus

- Quote validation failures in telemetry cannot allow a previously disallowed BUY.
- No extra write before posting accepted trades beyond the existing pre-POST stage transaction.
- No credentials, preview IDs, signatures, authorization headers or unrelated API payloads persisted.
- Old DB files migrate without destroying journal uniqueness.
- Unknown fill outcome remains unknown, never "filled" or "profit".
- Rejected value-gate observations cannot themselves create a market order record or prevent later valid preview evaluation.

**Research warning:** Expected net benefit based on model confidence is not empirical calibrated probability; this is data collection, not evidence of a stronger AI or real-world profitability.
