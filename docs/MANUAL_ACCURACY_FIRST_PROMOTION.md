# Manual activation of a verified, accuracy-favored rejected KalshiEdge model

**This is a user-controlled override of deployment policy, not an automatic-promotion pass.**

The automatic full-model gates remain strict and unchanged. A manually selected model preserves its original rejected evaluation result, complete metrics, failure reasons, source experiment hash, and a distinct `kalshi-manual-accuracy-override-v1` rule in the active promoted bundle. Hash-sealed previous models are preserved for rollback.

## Why the November 14 full retrain was not automatically promoted

On 1,222 settled 480-second markets, the fresh candidate had **382** high-confidence qualifying predictions with **91.4%** qualified accuracy versus the active model's **405** at **91.1%**. It made **349** correct qualified predictions versus the active model's **369**. On all 1,222 markets, the paired direction result improved by **11** predictions (**+0.900164 percentage points**, 28 candidate-only correct versus 17 incumbent-only correct).

That is an observed direction advantage, not proof of superiority: the saved paired 95% bootstrap interval is approximately **[-0.164, +1.964] percentage points**, and exact McNemar p = **0.135**. Coverage declined by **23** qualifying markets. Brier/log loss did not beat the incumbent under the automatically predeclared stricter promotion rule. **Observed profit improvement has not been established.**

Only the following three original rejection causes are eligible for manual acceptance:

- `INCUMBENT_MODEL_NOT_BEATEN`
- `RETRAIN_480_INCUMBENT_NOT_BEATEN`
- `RETRAIN_480_COVERAGE_NOT_INCREASED`

Any leakage, missing source, broken schema, calibration degradation, missing benchmark, inadequate holdout, unverified paired report, missing candidate experiment, incompatible code, or modified automatic decision **cannot be overridden with this command**.

## Workflow — run on your own laptop only after current training is COMPLETE

**Do not update/restart a running worker. Stop AutoTrade before activating the model, and consider read-only/shadow operation before risking capital.** GitHub merges alone cannot change your local active predictor.

1. Update GitHub main only after the current full training has finished and its saved report is intact:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
```

2. Read-only inspection. The command verifies the SHA-bound local experiment, its saved one-time evaluation decision, the 480s paired-market audit, current active bundle and exactly allowable rejection reasons. It **does not re-score the used independent lockbox**:

```powershell
& $py -m kalshi_edge.bootstrap.manual_promotion preview
```

3. If the displayed hash and observed metrics match the candidate you want, explicitly activate **exactly that inspected hash**:

```powershell
$preview = & $py -m kalshi_edge.bootstrap.manual_promotion preview | ConvertFrom-Json
if (-not $preview.eligible) { throw "The rejected candidate is not safe to manually activate" }

& $py -m kalshi_edge.bootstrap.manual_promotion activate `
    --expected-experiment-sha $preview.experiment_sha256 `
    --accept-manual-risk

if ($LASTEXITCODE -ne 0) { throw "Manual promotion failed" }
```

This does not place trades or start the predictor. It hashes and copies the verified candidate into `models/promoted`, stores the previous `default.json` pointer in `models/manual_backups`, saves a separate audit record in `reports`, and atomically changes the local `models/default.json` pointer. **Restart KalshiEdge** for the new model to be loaded. Verify active and installed hashes match before enabling AutoTrade again.

4. To **restore the original model** from the saved pointer:

```powershell
$pointer = Get-Content ".\data\bootstrap\models\default.json" -Raw | ConvertFrom-Json
& $py -m kalshi_edge.bootstrap.manual_promotion rollback `
    --expected-active-hash $pointer.bundle_sha256 `
    --accept-manual-risk
```

Restart KalshiEdge to load the restored model. The original and challenger bundles remain hash-sealed on disk. The historical rejected report remains unchanged and can still be audited.

## What this does NOT do

- It does **not** retroactively convert the original lockbox failure into an automatic success.
- It does **not** loosen automatic promotion policies for subsequent training, invent accuracy metrics, re-score used holdout markets, change the 69% confidence gate, change market settlements, or submit orders.
- It does **not** demonstrate statistical or realized profit superiority. A controlled forward comparison is still useful.
- It does **not** promote a TimesFM-3 research model or override its noncommercial licensing.
- It does **not** access local files from GitHub CI. The user must run the explicit local command after deciding whether to accept the observed uncertainty and reduced coverage.
