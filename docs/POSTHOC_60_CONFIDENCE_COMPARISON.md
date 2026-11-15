# Compare Model 1 and Model 2 at 60% confidence (historical, post-hoc only)

The November 14 full-model evaluation on **1,222 exact 480-second markets** stored gate aggregates and paired ticker identities at a **69% confidence** threshold. It did **not** store the 1,222 underlying Model 1 and Model 2 probability vectors. **Exact 60%-gate accuracy and coverage cannot be computed from the saved report alone.**

This command recreates the old 480s model outputs from the original saved hash-verified archive **only after deliberate consent**. It computes both models at **60% and 69%**, and refuses to report any result unless the original **69%-gate qualified and correct counts match exactly**.

## What 60% means

KalshiEdge's confidence is `2 * abs(p_yes - 0.5)`. The 69% gate means `p_yes <= 0.155` or `p_yes >= 0.845`. The 60% gate means `p_yes <= 0.20` or `p_yes >= 0.80`. This is NOT a requirement that `p_yes >= .60`.

For both models, output contains qualifying forecasts, correct/incorrect, skipped, coverage, qualified accuracy, change in qualified count from 69%, and all-market accuracy. Both models are scored against the **same** verified market ticks and official settlements. The 60% overlap (both qualified / model1-only / model2-only / neither) is included.

## Critical interpretation

This is an **exploratory re-analysis of already-used holdout data**. The user saw the 69% result before asking for 60%. Therefore a 60% result does **not** count as new independent evidence, a promotion-rule justification, or an expected-profit estimate. Use future settled markets for an independent test if you choose a threshold based on these results.

Neither the original `reports/evaluation-...` document, nor `models/default.json`, nor AutoTrade thresholds, balance, trading journal, training progress, or model bundles is changed. The command writes the JSON result to standard output and progress to standard error.

## PowerShell

**Only after the current training worker has finished**, and with AutoTrade off and the app closed to avoid CPU/GPU contention:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"

# No changes to promotion, trading or confidence settings.
& $py -m kalshi_edge.bootstrap.posthoc_confidence_comparison --acknowledge-posthoc
if ($LASTEXITCODE -ne 0) { throw "Historical 60% comparison failed; do not infer its outcome" }
```

The printed JSON is the real comparison. Copy it back into the chat for interpretation, including the 60% coverage and accuracy table.

**The command can take substantial CPU/GPU time** because each 1,222-market model's structural probabilities must be recomputed. It uses the existing bounded multicore/GPU backend and prints row counts to stderr.

## Required reproducibility checks

- The run must still be the original completed baseline run in `data/bootstrap/training_runs/current.json`. It must reference the original hash-addressed report.
- Original 1,222-market candidate experiment bundle and Model1 promoted bundle must still be saved and loadable with compatible code. If model2 is active, the previous pointer must identify the original model1 hash. If model3 or higher is active, the command refuses to guess.
- The local `derived/features.parquet` and its signed manifest/provenance SHA must **exactly equal the dataset SHA used in the original report**. If the dataset has since been rebuilt with more history, the replay stops; it will not silently compare another sample.
- The exact original 69% qualified/correct/total counts for *both* models must be reproducible before 60% data is shown. If runtime libraries or cached model behavior have changed, stop, don't treat the new result as valid.
- No raw probability arrays are dumped, moved to GitHub, used for training, or repurposed as fresh lockbox evidence.

This intentional post-hoc analysis is separate from `promotion_audit.py`, whose saved paired-report reader never replays a consumed lockbox.
