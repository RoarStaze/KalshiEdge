# KalshiEdge

Production-oriented foundation for a **KXBTC15M Empirical Edge Engine**. The repository contains two deliberately isolated, read-only capabilities:

1. the canonical **Phase 1 collector** for immutable Kalshi/BRTI acquisition, integrity verification, and deterministic replay; and
2. a parallel **bootstrap hybrid predictor** that backfills official Kalshi/Binance history, builds leakage-safe point-in-time features, trains chronologically validated probability models, applies an untouched lockbox promotion gate, and can emit live ABOVE/BELOW probabilities from a promoted model.

Neither capability contains order-placement, cancellation, portfolio-mutation, or live-trading authorization.

## Phase 1 collector

Phase 1 captures:

- KXBTC15M open-event/market metadata from Kalshi REST;
- authenticated `orderbook_delta` snapshots/deltas and public trade updates;
- CF Benchmarks `BRTI` values and final-minute settlement-window observations;
- market lifecycle events and determined/settled snapshots;
- source/receive timestamps, connection-scoped SID/SEQ, canonical payload hashes, exact WebSocket-frame hashes/payloads, and schema-versioned envelopes;
- credential-free runtime/config snapshots for reproducibility and data lineage.

Canonical raw ingress is stored under `data/raw` as finalized JSONL segments with SHA-256 sidecars. Sequence gaps, duplicates, out-of-order messages, malformed frames, and stale feeds fail closed. Dataset verification rejects corrupt hashes or sequence/integrity violations, and replay is deterministic.

## Bootstrap hybrid predictor

The bootstrap path is physically and logically separate under `data/bootstrap/`. It uses:

- official historical Kalshi KXBTC15M outcomes, trades, and candles;
- official Binance BTCUSDT 1-second history as an external feature source, never as the settlement label;
- fixed point-in-time checkpoints with causal timestamp enforcement and automated leakage auditing;
- chronological market-grouped development/calibration/lockbox partitions;
- structural settlement modeling, regularized/nonlinear candidates, residual correction, nonnegative stacking, and calibration;
- SHA-256 sealed experiment/promoted model bundles with provenance;
- a conservative promotion gate against contemporaneous Kalshi probability;
- an isolated live Kalshi/Binance inference process that returns `NO_PREDICTION` when required feeds/model inputs are missing, stale, malformed, or causally incomplete.

The bootstrap predictor does **not** bypass the Phase 1 gate and does not constitute Phase 2 completion or permission to trade.

## Install

Collector/runtime environment:

```bash
python -m venv .venv
# activate the environment
pip install -r requirements.lock
pip install --no-deps --no-build-isolation -e .
```

For bootstrap research/training, additionally install the separate research lock:

```bash
pip install -r requirements.research.lock
```

Research dependencies are intentionally excluded from `requirements.runtime.lock`, which remains the production collector dependency surface.

Copy `.env.example` to `.env` locally and provide the Kalshi API key ID plus the path to the local RSA private key. Never commit secrets.

## Commands

Phase 1:

```bash
kalshi-edge collect
kalshi-edge verify-dataset ./data
kalshi-edge phase1-report ./data
kalshi-edge verify-segment path/to/segment.jsonl path/to/segment.jsonl.sha256
kalshi-edge replay path/to/segment.jsonl KXBTC15M-...
kalshi-edge replay-dataset ./data KXBTC15M-...
```

Bootstrap predictor:

```bash
kalshi-edge bootstrap-backfill --source kalshi
kalshi-edge bootstrap-backfill --source binance
kalshi-edge bootstrap-backfill --source all
kalshi-edge bootstrap-build-dataset
kalshi-edge bootstrap-train
kalshi-edge bootstrap-evaluate
kalshi-edge predict-live
```

`bootstrap-evaluate` is the only path that may promote an experiment to the live default. `predict-live` accepts only a promoted/default hash-verified model bundle.

## Unified local dashboard (Windows)

After a v5 model has been promoted, launch KalshiEdge as a **single-command**, read-only local application. It runs the existing live prediction engine, settlement synchronization, and a combined browser dashboard with:

- a live changeable UP/DOWN preview, price-to-beat, BTC reference, confidence meter and time-to-close;
- an immutable ABOVE/BELOW/SKIP final at **240 seconds before close**, using the actual **69% confidence gate**;
- an automatically updating settled history, directional accuracy, SKIP/pending counts, and Kalshi-confirmed outcomes.

Install the per-user command **once** from the repo root (requires the existing `.venv`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-kalshiedge.ps1
```

Open a **new** PowerShell or Command Prompt window from **any folder**, then run:

```text
kalshiedge start
```

This opens **http://127.0.0.1:8899/** in your default browser. Keep that terminal open. Ctrl+C stops the services that the launcher started. `kalshiedge start --no-browser` and `kalshiedge start --port 8900` are optional. The legacy `kalshi-edge predict-live` and `kalshi-edge final-history` commands still work; stop old manual instances before switching to one-command startup.

The installer writes `%LOCALAPPDATA%\KalshiEdge\bin\kalshiedge.cmd` and adds that directory to the **user PATH**; administrator permissions are not required. The wrapper invokes your repository's `.venv` directly. Logs: `data/bootstrap/diagnostics/app-*.log`. The dashboard binds only to `127.0.0.1`. The launcher never initiates trading, training, backfills or lockbox evaluation. Model confidence is not measured win-rate accuracy.
## Desktop Control Center

An optional Windows desktop GUI is available for the bootstrap workflow. Install and launch it from the repository root:

```powershell
python -m pip install -e ".[gui,research]"
kalshi-edge-gui
```

The UI shows the full bootstrap pipeline, persists stage state locally, and avoids invented progress percentages when a command does not report measurable progress. See [the Control Center runbook](docs/RUNBOOK_CONTROL_CENTER.md) for its current capabilities and resume limitations.

## Runbook and project state

For exact Windows commands, artifact locations, model/provenance verification, predictor start/stop instructions, and the independent Phase 1 collector safety boundary, read [`docs/RUNBOOK_BOOTSTRAP_PREDICTOR.md`](docs/RUNBOOK_BOOTSTRAP_PREDICTOR.md).

Read `docs/PROJECT_CONTROL.md` for the authoritative gate/state summary. The Phase 1 gate must remain `NOT PASSED` until its acceptance evidence is actually satisfied, regardless of bootstrap model performance.

## Source verification

`docs/VERIFICATION_REPORT.md` records the official Kalshi/CF Benchmarks interfaces verified on 2026-08-31. Re-verify current first-party documentation whenever API or contract rules change.