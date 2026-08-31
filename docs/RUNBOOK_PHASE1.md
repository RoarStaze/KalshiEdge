# Phase 1 Collector Runbook

## Security

Never commit the RSA private key, `.env`, raw data, or runtime secrets. The repository ignores common key extensions and `.env`.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps --no-build-isolation -e .
```

Create a local `.env` from `.env.example` and set:

```text
KALSHI_KEY_ID=<your key id>
KALSHI_PRIVATE_KEY_PATH=<absolute path to RSA private key file>
KALSHI_ENV=production
KALSHI_DATA_DIR=./data
```

## Optional production container

Build with the exact Git commit embedded in the runtime snapshot:

```bash
docker build --build-arg BUILD_GIT_SHA=$(git rev-parse HEAD) -t kalshi-edge .
```

Mount `/data` for durable raw storage and mount the RSA private key read-only outside the image.

## Run read-only collection

```bash
kalshi-edge collect
```

The collector does not contain order-placement code. It records REST metadata and credential-free runtime snapshots, subscribes to BRTI/lifecycle even if no market is currently open, adds orderbook/trade subscriptions for active KXBTC15M markets, and reconnects on integrity anomalies, stale data, or target lifecycle changes.

## Verify the Phase 1 dataset

```bash
kalshi-edge verify-dataset ./data
```

Exit code `0` means all finalized segments found by the verifier have valid segment, payload, and wire hashes; supported schema versions; parseable records; and no detected gap/duplicate/out-of-order conditions. A successful command alone does **not** pass Phase 1 unless the duration/market-count/reconnect/BRTI/settlement criteria in `docs/PROJECT_CONTROL.md` are also evidenced.

## Evaluate machine-checkable Phase 1 evidence

```bash
kalshi-edge phase1-report ./data
```

The report checks the configured 24-hour/90-window evidence threshold, multiple connection sessions, BRTI/final-minute average coverage, lifecycle and settlement snapshots, runtime snapshots, and deterministic replay. It also lists manual operational confirmations still required.

## Replay a market

```bash
kalshi-edge replay-dataset ./data KXBTC15M-...
```

Run the same replay twice and compare `state_hash`. Accepted Phase 1 data must reproduce identically.

## Failure handling

- Any sequence gap, duplicate, out-of-order record, malformed WebSocket frame, or stale-feed timeout: collector fails the connection closed and reconnects for a fresh snapshot.
- WebSocket disconnect: exponential reconnect backoff.
- Hash mismatch: quarantine the affected segment from research; do not rewrite it.
- Missing BRTI or lifecycle coverage: mark the affected market interval invalid for strict research.
