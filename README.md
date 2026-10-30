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
## Future learning from mistakes (settlement-gated)

After an official Kalshi market settlement, KalshiEdge automatically inspects the **immutable locked final forecast**, not its changing live preview. The future training pool accepts exactly two categories:

- **Incorrect directional final forecasts:** a locked ABOVE/BELOW whose official Kalshi YES/NO settlement proves it wrong.
- **Low-confidence / low-probability final forecasts:** a saved, on-time final estimate with model confidence below **69%**, or directional probability below **69%**. The existing 69% final-decision gate normally makes these `SKIP` decisions; unresolved, stale-feed and late SKIPs are excluded.

Correct **high-confidence** markets and unresolved/provisional outcomes are never enrolled. Each selected market is stored once under `data/bootstrap/mistake_pool/YYYY-MM-DD/<ticker>.json`, bound to the original immutable forecast and officially verified outcome by SHA-256 hashes; the source files are not changed.

After merging this policy, from the repository virtualenv you can inspect the queue without retraining:

```powershell
cd "$HOME\Desktop\KalshiEdge"
.\.venv\Scripts\Activate.ps1
kalshi-edge final-history --once
kalshi-edge mistake-pool
```

**Future `kalshi-edge bootstrap-train` calls now default to mistake-only model fitting** when using this CLI. The new training policy:

- Fits candidate models only on verified mistake/low-confidence markets **whose causal historical features were subsequently backfilled and rebuilt into the verified dataset**.
- Keeps *later representative calibration markets*, including correct examples, for separate probability calibration and an independent, newer, untouched evaluation lockbox. Calibration is intentionally not mistake-only; otherwise probability estimates can become badly biased.
- Requires at least **12 eligible selected development markets** with both settlement classes, and at least **20 genuinely new markets** after the last evaluated lockbox. Without sufficient data, training **fails before expensive model fitting**; the existing promoted model remains in service.
- Preserves the one-time Kalshi benchmark promotion gate. No automatic model replacement or trading happens.

To gather new causal training features, separately run the existing appropriate Kalshi/Binance historical backfills and `kalshi-edge bootstrap-build-dataset` after newly settled markets become available. Enrollment alone does not fabricate feature rows or bypass an exhausted lockbox.

The preexisting full-history initial bootstrap remains available **only before any promoted default model exists** via `kalshi-edge bootstrap-train --initial-baseline`. After promotion, that flag is refused. The installed `kalshiedge start` launcher continues normal read-only live collection and settlement enrollment without launching training.

**Important:** Failure-only sampling is statistically biased: a model trained solely on failures may lose existing strengths and predict poorly calibrated probabilities. This is an experimental hard-example policy, not a demonstrated accuracy improvement. Keep independent population-representative calibration and untouched lockbox tests; promotion is not guaranteed.
## Volatility-aware final probability challenger

A separate model upgrade tests whether realized BTC volatility adds measurable predictive value at the **locked four-minute checkpoint**, without refitting the entire expensive V5 outcome model or disturbing the 3:1 training cycle.

- Uses only point-in-time `btc_close`, Kalshi strike, 60s and 300s realized volatility, and time remaining. A simple lognormal approximation estimates the chance that **Kalshi's final 60-second settlement average** finishes above the strike (rather than incorrectly using only the last spot price).
- On historical **240-second calibration rows**, selects a fixed conservative blend weight with a chronologically earlier fitting block and a later acceptance block. If it doesn't improve Brier and log loss with bounded calibration regression, the experiment is rejected **before accessing any fresh lockbox**.
- If it passes calibration, creates a SHA-256-bound **volatility challenger bundle** pinning the currently promoted V5 model, dataset provenance, selected weight, and independent future lockbox. The challenger does not change any final forecast previously saved.
- During a fresh lockbox evaluation, compares the volatility candidate against **Kalshi and the currently promoted incumbent** both on standard checkpoints and on **exactly one 240s prediction per market**. Directional accuracy must not degrade; probability metrics must improve without violating calibration tolerances. **Only then** will it automatically create a promoted model and atomically update `models/default.json`; otherwise it preserves the old model.
- The existing 69% strength/confidence rule and exact 240-second final checkpoint are unchanged. The dashboard shows a promoted upgraded model's estimated BTC move (±1σ) and volatility blend weight; model probabilities still require ongoing live calibration monitoring.

Before running, a complete new historical period of **at least 120 genuinely new KXBTC15M markets beyond every previously evaluated lockbox** must be backfilled and rebuilt into the causally verified dataset. Previously tested lockboxes cannot be reused. This is separate from the dashboard's mistake/refresher training readiness and never places trades.

Check readiness **without using the lockbox**:

```powershell
cd "$HOME\Desktop\KalshiEdge"
.\.venv\Scripts\Activate.ps1
kalshi-edge volatility-upgrade --check
```

Once the new dataset reports ready, the following **explicit command trains the volatility challenger and runs the one-time fresh lockbox comparison**, automatically replacing the model only on pass:

```powershell
$gitSha = (git rev-parse HEAD).Trim()
kalshi-edge volatility-upgrade --git-sha $gitSha
```

Some upgrades may find no reliable validation improvement and **leave the lockbox untouched**. A rejected fresh lockbox candidate consumes that evaluation period for future model selection. After promotion, restart `kalshiedge start` to load the new hash-addressed default bundle. The earlier promoted model remains intact for auditing and rollback. A conditional promotion is not a guarantee of improved future live predictions.
## Adaptive 3:1 training cycle and dashboard readiness

After the initial promoted baseline, future learning uses a **repeating four-session curriculum**:

1. Mistake round: train only on previously incorrect or legitimately low-confidence settled final forecasts.
2. Mistake round: repeat with the eligible historical error pool.
3. Mistake round: repeat with the eligible historical error pool.
4. Correct refresher: train only on previously **correct, high-confidence** settled final forecasts.

Then repeat. The **cycle advances only when the experimental model has completed its one-time fresh lockbox evaluation**—including if promotion is rejected. Training failures, pending evaluation and a restarted UI do not advance the cycle. Correct cases live in a separate verified, immutable `data/bootstrap/correct_pool/` directory; they are never marked as mistakes. Both sets bind the original locked prediction and official settled market result with SHA-256.

The unified dashboard adds an **Adaptive Learning** panel showing the current round and 3:1 pattern, a **collection readiness bar**, counts of eligible markets (minimum 12), both settlement outcome classes, and at least 20 genuinely new markets beyond the last consumed lockbox. **100% means all preconditions and dataset integrity checks passed**, not a prediction win-rate or training completion. If causal historical feature data is absent, backfill and rebuild the dataset first; a saved final forecast alone is not a model input.

When ready, the **Train Now** button becomes enabled. Clicking it and accepting the explicit confirmation starts the supervised CPU-prioritized training worker while the local dashboard and live predictor continue running. It trains the selected development markets, calibrates probabilities on a **later representative period**, and performs the **one-time untouched lockbox evaluation**. Promotion is permitted only if the candidate beats **both Kalshi's benchmark and the incumbent promoted model** on that fresh evaluation. Rejected candidates do not replace the live model. The dashboard displays worker stage and outcome, but never fabricates completion percentages for long fitting.

Training may take hours. Keep `kalshiedge start` running; Ctrl+C stops the launched training worker as well as the local services. New promoted model bundles are picked up by the live predictor after a restart of KalshiEdge. Logs are saved under `data/bootstrap/training_runs/` and stage metadata under `training_runs/current.json`.

**No automatic trading, silent retraining, lookahead selection, reuse of previously evaluated lockboxes, or automatic promotion without passing evaluation.** This 3:1 scheduling hypothesis is not proven to improve out-of-sample accuracy. Monitor the measured live final-forecast accuracy and probability-calibration metrics before treating it as an improvement.
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
## AutoTrader (paper-only local preview)

A separate loopback-only trading research page is available after installation:

```powershell
# Keep `kalshiedge start` running in another terminal.
python -m kalshi_edge.trading
```

Open **http://127.0.0.1:8898/** or follow **AutoTrader (paper)** from the
main dashboard. It reads V5's immutable 240-second final forecast, checks
Kalshi's public market and orderbook data, and simulates a maximum **$0.10
all-in stake** with a **$0.50 daily paper loss circuit breaker**. Optional
Kalshi credentials may be encrypted locally with Windows DPAPI via the page;
**they are not used to submit orders**. This release has **no live-order
endpoint** and does not claim profitable performance.

See [AutoTrader design, safeguards, and limitations](docs/autotrader-paper.md).
