# Continue training the active KalshiEdge model (no full retrain)

## Why the previous Train Now often failed

The Train page previously chose a whole-model baseline retrain if at least 80 **genuinely new** market groups were available for a new independent lockbox. If not, it ran a standalone 480s logistic research challenger. The research path is permanently `ready_to_promote=False`, so it cannot advance Model #2. Repeating that training does not progressively update the deployed classifier.

As of PR #155, new research comparisons and new confidence-gate labels use the **63%** live threshold. Historical saved reports explicitly created at **69%** remain 69%, since changing their label would falsify history.

## New Train Now: Continue Training

On installations with a valid promoted model pointer, **Continue Training** is the sole main training action. It uses the current bundle and:
- Reads its serialized, verified active fitted classifier.
- Applies a small number of **new** booster iterations using the saved XGBoost or histogram-gradient-boosting model as starting weights, and updates existing logistic regression coefficients with warm start and the original frozen feature scaler.
- Keeps fitted structural, residual, stacking and calibration objects unchanged. They are not retrained.
- Uses only original immutable 480-second snapshots joined to authenticated **official Kalshi settlements**; no price features are reconstructed retrospectively. Requires the saved final forecast's model hash to match the **currently active** model. Previously used lockbox markets cannot be treated as new again.
- Saves the continued experiment in an immutable SHA-addressed file **before** opening its new chronological validation cohort.
- Compares current versus continued pipeline on 80 later settled markets at **63% confidence** under the same v3 coverage-first acceptance rule. If the continued candidate issues **more** qualified signals with an acceptable small accuracy/probability-quality decline, it is promoted as a **new numbered model**, with the old one saved for rollback.
- If promotion fails, training is **not discarded**: the continued fitted experiment is kept under `data/bootstrap/models/continuation/latest.json` and the next eligible run extends those weights again. The currently active model is unchanged. A test rejection is displayed as `TRAINED_NOT_PROMOTED` rather than a generic model-training failure.
- Train Now never automatically falls back to the expensive whole-dataset baseline trainer or undeployable 480s research specialist while an active model exists. Those remain separate explicit research/bootstrap paths.

## Data requirements

**Verified assumption for incremental v1:** At least **130 genuinely new** complete, settled, hash-verified 480-second forecasts made by the **currently active model** after the latest used independent evaluation boundary, with both outcome classes. The split is chronological: at least **40 development markets**, **10 independent calibration-period markets**, and **80 fresh validation markets**. The 80-market validation minimum matches the existing v3 promotion rule and avoids silently weakening it to increase promotion frequency. Training can be fast, but new outcome evidence still takes real market time. The old 634/120 research readiness count from the screenshot is **not** interchangeable with the active Model #2 fresh-cohort count.

**Limitation:** This is true warm-start continuation of the classifiers, not complete incremental re-optimization of every component. It retains the historical feature map, structural model, residual correction, stacker, and calibrator exactly. A full model retrain would still be required after a feature-schema change or if those other components need relearning. The first continuation may not be ready immediately following Model #2 activation.

## Windows PowerShell after PR is merged, when no worker is running

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Read only. Does not train or trade.
& $py -m kalshi_edge.bootstrap.model_continuation
```

Open KalshiEdge **Train**. The main button is now **Continue Training** when enough **new current-model** settled markets are ready. It uses the existing app's Pause and Resume controls. AutoTrade stays under the user's explicit control, and no update or model-promotion command submits exchange orders.

On a successful validation, restart KalshiEdge so its in-memory live model loader adopts the new numbered bundle. On a rejected validation, Model #2 remains active while the next training step starts from the saved continued checkpoint.

## Important financial limitation

Increasing confidence-gate forecast coverage is not the same as increasing **realized net return**. Transaction price, liquidity, fills, fees, direction conflicts and Kalshi payout economics are not established by this training gate. A fresh 80-market comparison is noisy; monitor forward performance and verified realized P/L before risking more funds.
