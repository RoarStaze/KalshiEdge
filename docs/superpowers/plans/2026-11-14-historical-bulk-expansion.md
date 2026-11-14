# Historical-First Training Dataset Expansion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and verification-before-completion before merging.

**Goal:** Let the user import the largest supported historical KXBTC15M dataset immediately, using official Kalshi settled markets/candlesticks/trades and Binance 1s BTC history, then build hash-verified point-in-time feature rows without any model promotion or replayed evaluation.

**Architecture:** Reuse the existing `backfill_kalshi`, `backfill_binance`, and `build_dataset` functions in strict order. Add an explicit `bootstrap-expand-history` CLI orchestration command with date/market budget controls and real per-market/per-day progress. Make all completed artifacts reusable on subsequent runs (existing backfill behavior), and do not change feature construction, leakage policy, chronological holdouts, or trading.

**Tech Stack:** Python 3.12, argparse, httpx, Pydantic, official Kalshi historical REST API, Binance 1-second archive, Parquet and SHA-256 provenance.

**Spec:** User requests historical archived BTC market data be gathered in bulk to train KalshiEdge now rather than waiting for prospective live data.

## Global constraints
- Read-only Kalshi market API and official Binance public archives only.
- One-second Binance bars and one-minute Kalshi candles are not retroactively recorded order-book microstructure.
- Original 480-second feature-time and settlement label rules are unchanged.
- Never overwrite a corrupt existing artifact, silently skip broken provenance, train a model, or promote a candidate as a side-effect.
- Do not claim this completes the user's Windows archive; downloading needs to be run locally by the user.

## Review focus
- Interrupted large import: repeated invocation skips fully verified files, restarts at missing file.
- Incorrect market-date controls: reject one-sided, inverted, and negative limits before network access.
- Market or archive missing: no invented checkpoint features or fake success.
- ZIP integrity: retain existing Binance checksum enforcement.
- Zero or stale market data: report missing eligible dataset rather than promoting a model.

## Tasks
- [ ] Add failing CLI orchestration regression for Kalshi → Binance → dataset and no unintended training.
- [ ] Add tests for exact Kalshi market and Binance day progress callbacks, including resumed/skipped and excluded records.
- [ ] Add `bootstrap-expand-history` with `--max-markets`, `--since-date`, `--until-date`; preserve `bootstrap-backfill` behavior.
- [ ] Add `progress_callback` support to both history import functions without affecting output data semantics.
- [ ] Run full CI and production image build; review changed file list; merge into `main`.
- [ ] Give PowerShell update, bulk import, and dataset report instructions.