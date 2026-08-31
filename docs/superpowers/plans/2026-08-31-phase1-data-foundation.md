# Phase 1 Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only KXBTC15M collector that preserves exact WebSocket/REST data, detects integrity failures, and supports deterministic replay.

**Architecture:** Public REST discovers open KXBTC15M markets and records metadata snapshots. An authenticated Kalshi WebSocket records orderbook snapshots/deltas, trades, BRTI, and lifecycle events into immutable hashed raw segments. Connection-scoped sequence validation fails closed on gaps; deterministic replay reconstructs order books from canonical raw events.

**Tech Stack:** Python 3.13, asyncio, websockets 16, httpx 0.28, cryptography 46, pydantic-settings 2, pytest 9, immutable JSONL raw segments.

**Spec:** `docs/superpowers/specs/2026-08-31-kxbtc15m-empirical-edge-engine-design.md`

## Global Constraints

- Phase 1 contains no order-placement code.
- Never commit API private keys or `.env`.
- Current official API documentation wins over this plan if schemas change.
- Every production behavior is test-first.
- Finalized raw segments are never mutated.
- Phase 2 is blocked until the Phase 1 gate in `docs/PROJECT_CONTROL.md` is evidenced.

---

### Task 1: Authentication and endpoint configuration

**Files:** `src/kalshi_edge/auth.py`, `src/kalshi_edge/config.py`, `tests/test_auth.py`, `tests/test_config.py`

**Interfaces:** `load_private_key()`, `create_auth_headers()`, `CollectorSettings`.

- [x] Write failing tests for RSA-PSS signing, query stripping, and environment endpoint selection.
- [x] Run tests and confirm missing implementation fails.
- [x] Implement minimal signing/configuration code matching official Kalshi docs.
- [x] Run tests and confirm green.

### Task 2: Protocol envelope, subscriptions, and sequence integrity

**Files:** `src/kalshi_edge/protocol.py`, `src/kalshi_edge/integrity.py`, `tests/test_protocol.py`, `tests/test_sequence.py`

**Interfaces:** `RawEvent`, `normalize_ws_frame()`, `normalize_ws_message()`, `normalize_rest_payload()`, `normalize_control_payload()`, `build_subscriptions()`, `SequenceTracker.observe()`.

- [x] Write failing tests for raw preservation, timestamps, BRTI/orderbook/trade/lifecycle subscriptions, and sequence classification.
- [x] Implement schema-versioned raw envelopes, exact WebSocket wire preservation, collector-session metadata, and connection-scoped sequence metadata.
- [x] Verify green tests.

### Task 3: Deterministic order-book reconstruction

**Files:** `src/kalshi_edge/orderbook.py`, `src/kalshi_edge/replay.py`, `tests/test_orderbook.py`, `tests/test_storage_replay.py`

**Interfaces:** `OrderBook.apply_snapshot()`, `OrderBook.apply_delta()`, `replay_orderbook()`, `replay_dataset()`.

- [x] Write failing tests for YES/NO bid ladders and reciprocal implied asks.
- [x] Implement Decimal-based book state and canonical state hashes.
- [x] Verify identical replay hashes for identical raw input, interleaved-market SID sequencing, and segment rotations.

### Task 4: Immutable raw segments and dataset gate verifier

**Files:** `src/kalshi_edge/storage.py`, `src/kalshi_edge/validation.py`, `tests/test_storage_replay.py`, `tests/test_validation.py`

**Interfaces:** `RawSegmentWriter`, `verify_segment()`, `verify_dataset()`, `evaluate_phase1_gate()`.

- [x] Write failing tests for SHA-256 segment verification and gap rejection.
- [x] Implement atomic segment finalization, nanosecond-sortable segment names, hash sidecars, per-event payload/wire verification, malformed/schema checks, and strict dataset verification.
- [x] Add connection-scoped sequence reset test to prevent false reconnect failures.
- [x] Verify green tests.

### Task 5: Live read-only collector and CLI

**Files:** `src/kalshi_edge/discovery.py`, `src/kalshi_edge/collector.py`, `src/kalshi_edge/cli.py`, `tests/test_discovery.py`, `tests/test_collector.py`, `tests/test_cli.py`

**Interfaces:** `KalshiCollector.run_forever()`, `MessageProcessor.process()`, `extract_market_tickers()`, CLI commands `collect`, `verify-dataset`, `phase1-report`, `verify-segment`, `replay`, `replay-dataset`.

- [x] Write failing tests for discovery filtering, lifecycle detection, fail-closed gaps, and CLI shape.
- [x] Implement REST discovery/metadata snapshots, authenticated WebSocket collection, lifecycle-triggered refresh, and settlement snapshots.
- [x] Verify green tests.

### Task 6: Reproducibility, CI, and runbooks

**Files:** `requirements.lock`, `Dockerfile`, `.github/workflows/ci.yml`, `.env.example`, `docs/*`.

- [x] Pin the Phase 1 dependency closure from the verified environment.
- [x] Add container and GitHub Actions test execution.
- [x] Document verified facts, assumptions, open questions, threat model, runbook, and authoritative project state.
- [ ] Run authenticated production collection for the Phase 1 validation window; this remains blocked until runtime credentials are supplied outside the repository.
