# Bootstrap Predictor Runbook

This runbook operates the **parallel read-only bootstrap predictor**. It does not replace the Phase 1 collector and it does not authorize trading.

## Safety boundary

- The Windows scheduled task **`KalshiEdge Phase1 Collector` is independent**. Do not stop, restart, reconfigure, or modify it when operating the bootstrap predictor.
- Bootstrap mode is `READ_ONLY_BOOTSTRAP_INFERENCE`: **no order placement**, no cancellation, no portfolio mutation, and no live-trading authorization.
- The Phase 1 acceptance gate remains separate. A promoted bootstrap model does not mark Phase 1 or Phase 2 complete and does not bypass any trading gate.
- Canonical Phase 1 data under `data\raw` must never be used as a bootstrap output directory.

## 1. Update and install on Windows

Open PowerShell in the existing clone:

```powershell
cd C:\Users\RoarStaze\Desktop\KalshiEdge
git status
git fetch origin
git checkout main
git pull --ff-only origin main

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    py -3.13 -m venv .venv
}
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install -r requirements.research.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
kalshi-edge --help
```

Do not delete or overwrite the ignored `.env` or private-key file during the update.

## 2. Credentials and secret handling

Kalshi read-only endpoints use the same local environment variables as the Phase 1 collector. Keep credentials only in ignored local files/environment variables. In particular:

- keep `.env` outside Git tracking;
- keep the RSA private key in an ignored local path;
- never paste the private key, API key ID, or private-key path into committed source, manifests, reports, prediction JSONL, screenshots, or logs;
- do not rotate, delete, or replace working credentials merely to operate the bootstrap predictor.

Check that Git will not commit local secrets before proceeding:

```powershell
git status --short
```

## 3. Backfill official historical data

```powershell
kalshi-edge bootstrap-backfill --source all
```

The command separately executes the official Kalshi and official Binance history paths. For isolated troubleshooting, the supported source selections are:

```powershell
kalshi-edge bootstrap-backfill --source kalshi
kalshi-edge bootstrap-backfill --source binance
```

Bootstrap raw inputs are stored under `data\bootstrap\raw\` with SHA-256 manifests under `data\bootstrap\manifests\`. They are separate from canonical Phase 1 `data\raw`.

## 4. Build the leakage-safe point-in-time dataset

```powershell
kalshi-edge bootstrap-build-dataset
```

Expected derived outputs include:

- `data\bootstrap\derived\features.parquet`
- `data\bootstrap\derived\features.provenance.json`
- `data\bootstrap\manifests\dataset\features.parquet.manifest.json`
- normalized Binance files under `data\bootstrap\normalized\binance\1s\`

The build fails closed if required input provenance does not verify or if the leakage audit finds a causal violation.

## 5. Train a development-only experiment

Use the exact checked-out Git SHA so the bundle records the source revision:

```powershell
$GitSha = (git rev-parse HEAD).Trim()
kalshi-edge bootstrap-train --git-sha $GitSha
```

Training writes experiment bundles under `data\bootstrap\models\experiments\` and reports under `data\bootstrap\reports\`. The training path does not evaluate the untouched lockbox and cannot create `models\default.json` by itself.

## 6. Evaluate the untouched lockbox and apply the promotion gate

```powershell
kalshi-edge bootstrap-evaluate
$EvaluateExit = $LASTEXITCODE
```

Interpretation:

- exit code `0`: the predeclared promotion rule passed and a hash-verified promoted/default bundle was created;
- exit code `2`: the candidate was evaluated but **not promoted**. Do not bypass the gate and do not start live inference with the experiment bundle;
- any other nonzero exit: treat as an execution/integrity failure and inspect the emitted error before proceeding.

Promoted artifacts are under:

- `data\bootstrap\models\promoted\`
- `data\bootstrap\models\default.json`
- `data\bootstrap\reports\evaluation-*.json`

## 7. Verify dataset and model provenance

Verify the dataset artifact against its manifest:

```powershell
python -c "from pathlib import Path; from kalshi_edge.bootstrap.provenance import verify_artifact; root=Path('data/bootstrap'); ok=verify_artifact(root/'derived/features.parquet', root/'manifests/dataset/features.parquet.manifest.json'); print({'dataset_verified': ok}); raise SystemExit(0 if ok else 2)"
```

Verify that the default pointer resolves to a promoted, content-hash-verified Task 8 bundle:

```powershell
python -c "from pathlib import Path; from kalshi_edge.bootstrap.live import load_default_bundle; b=load_default_bundle(Path('data/bootstrap')); print({'stage': b.stage, 'model_version': b.model_version, 'bundle_sha256': b.bundle_sha256})"
```

If either command fails, do not start the predictor.

## 8. Start the read-only live predictor

Start only after `bootstrap-evaluate` promoted a model and `data\bootstrap\models\default.json` verifies.

Foreground mode:

```powershell
kalshi-edge predict-live
```

For a persistent PowerShell-launched process beside the untouched collector:

```powershell
New-Item -ItemType Directory -Force -Path .\data\bootstrap\logs | Out-Null
$Predictor = Start-Process `
    -FilePath "$PWD\.venv\Scripts\kalshi-edge.exe" `
    -ArgumentList "predict-live" `
    -WorkingDirectory $PWD `
    -RedirectStandardOutput "$PWD\data\bootstrap\logs\predict-live.stdout.log" `
    -RedirectStandardError "$PWD\data\bootstrap\logs\predict-live.stderr.log" `
    -PassThru
$Predictor.Id | Set-Content .\data\bootstrap\predict-live.pid
Get-Process -Id $Predictor.Id
```

Prediction records are written once per second under:

```text
data\bootstrap\predictions\YYYY-MM-DD\predictions.jsonl
```

A healthy record has `status="OK"`. Missing, stale, malformed, causally incomplete, or model-schema-mismatched inputs produce `status="NO_PREDICTION"` with an explicit reason.

## 9. Stop or restart only the bootstrap predictor

Stop the predictor by its recorded PID:

```powershell
$PredictorPid = [int](Get-Content .\data\bootstrap\predict-live.pid)
Stop-Process -Id $PredictorPid
Remove-Item .\data\bootstrap\predict-live.pid -ErrorAction SilentlyContinue
```

Restart it with the `Start-Process` block in section 8.

**Do not run `Stop-ScheduledTask` or `Start-ScheduledTask` against `KalshiEdge Phase1 Collector` as part of bootstrap predictor operations.**

## 10. Artifact locations

| Purpose | Location |
| --- | --- |
| Bootstrap immutable raw history | `data\bootstrap\raw\` |
| SHA-256/provenance manifests | `data\bootstrap\manifests\` |
| Normalized Binance 1s history | `data\bootstrap\normalized\binance\1s\` |
| Point-in-time feature matrix | `data\bootstrap\derived\features.parquet` |
| Dataset provenance | `data\bootstrap\derived\features.provenance.json` |
| Experiment bundles | `data\bootstrap\models\experiments\` |
| Promoted bundles | `data\bootstrap\models\promoted\` |
| Current promoted pointer | `data\bootstrap\models\default.json` |
| Training/evaluation reports | `data\bootstrap\reports\` |
| Live predictions | `data\bootstrap\predictions\YYYY-MM-DD\predictions.jsonl` |
| Predictor logs/PID | `data\bootstrap\logs\`, `data\bootstrap\predict-live.pid` |

## Operational rule

The bootstrap predictor is an evidence-generating, read-only probability engine. It has **no order placement**, cancellation, portfolio mutation, or live trading capability. Keep `KalshiEdge Phase1 Collector` running independently and continue evaluating the Phase 1 gate on its canonical dataset.