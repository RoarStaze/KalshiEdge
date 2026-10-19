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

The existing dataset builder currently does not emit this progress event, so its activity is shown as indeterminate until that worker is instrumented. This is intentional and more accurate than an estimated percentage.

## Stop and resume limitations

The GUI persists completed stages and the last stage state. Completed stages remain complete across GUI restarts. If a process is interrupted mid-stage, that stage currently restarts from its beginning when retried. It is **not yet correct to claim exact mid-stage resume for every command**. Exact resumption requires durable per-item checkpoints inside the backfill, dataset, training, and evaluation workers, plus input-fingerprint validation before reusing a checkpoint. Those workers must be instrumented before the GUI can guarantee resuming at the exact point of interruption.

The stop control terminates only a process launched by this GUI. It never sends commands to Windows Scheduled Tasks. In particular, it does not stop, restart, or reconfigure the independent `KalshiEdge Phase1 Collector` task.

## Safety

- Bootstrap data stays under `data\bootstrap\`.
- Canonical Phase 1 data under `data\raw\` is not an output target for this GUI.
- A successful training command is not a promotion decision.
- A candidate is not live-eligible unless the evaluation gate creates a valid promoted default bundle.
- Live inference is read-only. The GUI does not authorize trading.
- Stage completion is based on command exit status plus the dedicated dataset verification step; review generated reports and promotion decisions before proceeding.
