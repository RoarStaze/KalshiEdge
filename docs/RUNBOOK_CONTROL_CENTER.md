# Desktop Control Center

The Control Center is an optional local GUI for operating the bootstrap research pipeline. It is a UI for the existing commands, not a replacement for the collector or prediction code.

## Install and launch on Windows

From the repository root, with the project's virtual environment activated:

```powershell
python -m pip install -e ".[gui,research]"
kalshi-edge-gui
```

Or launch from the virtual environment directly:

```powershell
.\.venv\Scripts\python.exe -m kalshi_edge.control_center
```

The app saves orchestration state to `data\bootstrap\control-center\state.json`. Data under `data\` is local and ignored by Git.

## Current stages

1. Backfill official Kalshi and Binance history.
2. Build the point-in-time feature dataset.
3. Verify the feature artifact against its manifest.
4. Train a development-only experiment.
5. Evaluate the untouched lockbox and apply the existing promotion gate.

The GUI does not automatically start read-only live inference at the end of the pipeline. Live inference is a separate explicit action and requires a default model pointer. The GUI contains no order-placement or portfolio mutation controls.

## Progress reporting

The overall bar is a count of configured stages whose commands returned success, not an estimate of elapsed time. The per-stage bar remains indeterminate unless the command emits a structured `KALSHI_EDGE_PROGRESS {"completed": n, "total": n, "unit": "..."}` line. No percentage is invented when a worker does not expose measurable progress.

The dataset builder emits an exact count of markets processed out of the discovered settled-market list. Once the market loop finishes, the UI switches back to indeterminate activity while the leakage audit, Parquet write, manifest, and provenance finalization run. That is deliberate: market-loop completion is not the same as dataset completion.

## Stop and resume limitations

The GUI persists completed stages and the last stage state. Completed stages remain complete across GUI restarts. The dataset builder now writes a durable per-market checkpoint after each completed market and validates every recorded input hash before reusing rows; if the process stops mid-market, it recomputes that market and resumes from the last committed market boundary. A partial final checkpoint line is discarded safely. The historical backfill already skips existing artifacts after verifying their manifests. Training and evaluation do not yet expose internal resumable checkpoints, so an interruption in either of those stages restarts that stage from its beginning. The whole-program requirement for exact mid-stage resume is therefore **partially implemented, not complete**; do not represent it as guaranteed for every stage until those workers also support durable checkpoints and recovery tests.

The dashboard observes an already-running bootstrap training worker through its PID and log files, disables controls that could start a duplicate stage, and displays its output without attaching to or controlling the worker. The **Finish stage, then pause** control lets the current stage complete and prevents the next pipeline stage from starting; it does not terminate the worker. Closing the dashboard while it owns an active stage is blocked so the worker is not interrupted accidentally. The dashboard never sends commands to Windows Scheduled Tasks. In particular, it does not stop, restart, or reconfigure the independent `KalshiEdge Phase1 Collector` task.

## Safety

- Bootstrap data stays under `data\bootstrap\`.
- Canonical Phase 1 data under `data\raw\` is not an output target for this GUI.
- A successful training command is not a promotion decision.
- A candidate is not live-eligible unless the evaluation gate creates a valid promoted default bundle.
- Live inference is read-only. The GUI does not authorize trading.
- Stage completion is based on command exit status plus the dedicated dataset verification step; review generated reports and promotion decisions before proceeding.
