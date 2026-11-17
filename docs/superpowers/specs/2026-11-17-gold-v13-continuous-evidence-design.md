# GoldPredict V1.3 — continuous gold evidence collection

Date: 2026-11-17. Scope: `KXGOLD15M` only. Research-only; never place orders, train models, unlock a gate, fetch API keys or change Bitcoin.

## Goal

Persist a durable stream of point-of-receipt Kalshi gold market observations (listed quote snapshots, order-book bids and recent trades). Preserve a separate independent settlement-verification research track: price, times and final labels remain unverified until backed by authenticated Pyth origin and controlling contract semantics.

## Chosen design

Poll the official Kalshi read-only REST endpoints at a configurable, bounded interval (default 30 seconds), rather than creating a new WebSocket infrastructure or integrating with existing trading workers. Every cycle stores the existing gold quote artifact, a separately hashed microstructure artifact and an append-only hashed cycle event, including failures. Retain an OS-held single-process lock for safe restarts. Back off failures, including HTTP 429.

**Alternatives not selected:** a continuous WebSocket system would add reconnect/order-book-delta state complexity and may require credential handling; a single repeated `--capture-quotes` loop would omit order-book depth, trades and failure accounting.

## Evidence and safety

- Use only `GET /series/KXGOLD15M`, `GET /markets`, `GET /markets/{ticker}/orderbook` and `GET /markets/trades`. No authenticated exchange endpoints or trade operations.
- Preserve local request/receipt nanoseconds, server Date header where available, unmodified decoded JSON and SHA-256 filename. The timestamps do **not** prove first exchange-publication time.
- Read up to 2 active markets by default, maximum 8; one orderbook up to 10 price levels and one recent-trades page (max 100 entries) per market per cycle. Missing later trades are not silently backfilled.
- Reject cross-series, malformed fixed-point prices/quantities, duplicate trades and corrupted SHA evidence. On failure, retain any successfully collected quote artifact and append a reason-coded cycle event. No invalid partial microstructure artifact is written.
- Never interpret Kalshi quote data as `Metal.Index.1OZGOLD/USD` settlement prices.
- Keep `training_ready=false`, `dataset_ready=false`, `settlement_verified=false` on every new artifact. Existing dataset and XGBoost evidence gates remain unchanged.
- The collector only runs while its local Python process is running. Ctrl+C stops. Rerunning starts a new session and preserves prior data. This is disk restart-safe, not a Windows background service.

## Implementation / test gates

1. RED: `tests/gold/test_continuous_v13.py` specifies immutable microstructure, price validation, bounded cycles, 429 recovery, status hash verification, safe CLI dispatch and missing model/trading changes.
2. GREEN: implement `src/kalshi_edge/gold/continuous.py`; modify gold-only `audit.py` with `--collect-continuously` and `--collector-status`.
3. Run `python -m pytest -q tests/gold` and full repository tests through GitHub CI. Reject release on any failing gate.
4. Do not mark authenticated Pyth source, exact candle alignment or independently reconstructed settlement labels verified without new primary evidence.

## Out of scope

Pyth Pro credential access, automated Kalshi position management, gold training or live forecasts, retroactive backfills disguised as prospective data, Bitcoin changes, changing the existing eight-minute forecast checkpoint, or a false claim of confirmed contract settlements.
