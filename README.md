# KalshiEdge

Production-oriented foundation for a **KXBTC15M Empirical Edge Engine**. The project is deliberately phase-gated: the current code is **Phase 1 read-only collection + integrity + deterministic replay**. It contains no order-placement implementation.

## What Phase 1 captures

- KXBTC15M open-event/market metadata from Kalshi REST.
- Authenticated `orderbook_delta` snapshots/deltas.
- Public trade updates over the authenticated WebSocket session.
- CF Benchmarks `BRTI` values and final-minute quarter-hour average fields.
- Market lifecycle events, including rollover detection.
- Determined/settled market REST snapshots.
- Source timestamps, nanosecond receive timestamps, connection-scoped SID/SEQ, canonical payload hashes, exact WebSocket-frame hashes/payloads, and schema-versioned envelopes.
- A credential-free runtime/config snapshot per collector session for reproducibility and data lineage.

## Integrity model

Canonical raw ingress is stored as finalized JSONL segments with SHA-256 sidecars. Sequence gaps, duplicates, out-of-order messages, malformed frames, and stale feeds fail the active connection closed and force a fresh subscription/snapshot. Dataset verification rejects corrupt segment/payload/wire hashes, malformed records, unsupported schema versions, gaps, duplicates, or out-of-order messages. Order books are reconstructed with `Decimal` and hashed deterministically across segment rotations.

## Install

```bash
python -m venv .venv
# activate the environment
pip install -r requirements.lock
pip install --no-deps --no-build-isolation -e .
```

Copy `.env.example` to `.env` locally and provide your Kalshi API key ID plus the path to your RSA private key. Never commit secrets.

## Commands

```bash
kalshi-edge collect
kalshi-edge verify-dataset ./data
kalshi-edge phase1-report ./data
kalshi-edge verify-segment path/to/segment.jsonl path/to/segment.jsonl.sha256
kalshi-edge replay path/to/segment.jsonl KXBTC15M-...
kalshi-edge replay-dataset ./data KXBTC15M-...
```

## Project state and gates

Read `docs/PROJECT_CONTROL.md` first. Phase 1 is not considered complete until the live validation window is captured and all gate criteria are evidenced. Phase 2 research and all trading remain blocked until then.

## Source verification

`docs/VERIFICATION_REPORT.md` records the official Kalshi/CF Benchmarks interfaces verified on 2026-08-31. Re-verify current first-party documentation whenever the API or contract rules change.
