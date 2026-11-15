# KalshiEdge — active Model 2 at 63% and coverage-first next-model policy

**Intent:** Reduce SKIPs while protecting most of the observed directional accuracy. The user selected the saved Model 2 challenger and 63% minimum confidence for final prediction and explicitly armed AutoTrade.

## Verified historical observation (descriptive, not prospective)

Both models were compared at the exact 480-second checkpoint on the same 1,222 historical markets. At **63% confidence**, Model 1 produced 489 qualifying forecasts, 440 correct, 49 wrong, 733 skipped (**89.98% qualified accuracy; 40.02% coverage**). Model 2 produced 502 qualifying forecasts, 450 correct, 52 wrong, 720 skipped (**89.64% qualified accuracy; 41.08% coverage**). Model 2 had **13 fewer SKIPs and 10 more correct decisions, with 3 additional wrong forecasts**. This post-hoc historical result is not prospective validation or a trading-profit estimate.

## Live inference and trading policy (future markets)

- Only at the original immutable **480-second-before-close** checkpoint can the final forecast become ABOVE/BELOW. The model confidence floor is now **0.63**, replacing **0.69**.
- AutoTrade independently uses that **same imported 0.63 constant** for the final forecast before it considers an order. AutoTrade remains off until deliberately armed by the user. No trading is performed by training, PR merge, `git pull`, or the manual model activation CLI.
- Missing/stale/unhealthy prediction and market inputs, wrong ticker, the near-strike **coin-flip veto**, late checkpoint, missing economic quotes and other preflight/order safeguards remain unchanged.
- Live dashboard displays **63%** as the SKIP confidence floor. Newly saved final SKIPs use `CONFIDENCE_BELOW_63_PERCENT`; historical records with the prior `CONFIDENCE_BELOW_69_PERCENT` stay readable.

## Future model selection: coverage first with bounded accuracy decline

Automatic promotion for **new full retraining** uses a distinct rule `kalshi-lockbox-v3:coverage-first-63pct-bounded-accuracy-and-probability-loss`. It evaluates the challenger and incumbent on the **same new, untouched** 480-second lockbox markets, both at **63%**. The new candidate must have **strictly more qualifying forecasts** (fewer SKIPs) and satisfy:

- **Qualified accuracy** at most **0.5 percentage points** below the current model.
- **Overall 480s directional accuracy** at most **1.0 percentage point** below the current model.
- **Brier score** no worse than current by more than **0.005**, and **log loss** by no more than **0.02**, at both full sample and 480-second same-market comparisons.
- **ECE/calibration** no more than existing **+0.02** allowed degradation, original Kalshi benchmark superiority and all leakage/schema, nondegenerate prior, required component/ablation, sufficient new data and independent-lockbox safeguards still required.

These tolerance levels were predeclared as conservative interpretations of \"slightly less accurate,\" not optimized on the previously viewed 1,222-market historical outcomes. The accepted objective is higher **coverage**, not a requirement for higher accuracy on every train. Historical v2 decisions and Model 1/2 bundles retain their original saved rule and are not retroactively upgraded. Separate volatility research challengers retain their independent strict tests, including their original 69%-gate diagnostics, unless revised in a separate prospective design.

**Limitation:** This updates the deployment-selection policy, not the core model's fit/calibration optimizer. Existing fit is still based on probability-quality loss; a future development-only coverage-aware candidate search would require new isolated tuning evidence. Do not optimize tuning on an already-consumed evaluation lockbox.

## Activate exact saved Model 2 on your Windows laptop

**Turn AutoTrade off and fully close KalshiEdge before updating.** Do not modify a running training worker. Model activation requires the local saved experiment and evaluation report from the original full training; GitHub doesn't have those local private artifacts.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
$preview = & $py -m kalshi_edge.bootstrap.manual_promotion preview | ConvertFrom-Json

if ($LASTEXITCODE -ne 0 -or -not $preview.eligible -or
    $preview.evaluated_markets -ne 1222 -or
    [math]::Abs($preview.paired_accuracy_delta_pp - 0.900163666121113) -gt 0.01) {
    throw "Saved Model 2 candidate did not match the verified historical report"
}

$preview | Format-List

& $py -m kalshi_edge.bootstrap.manual_promotion activate `
    --expected-experiment-sha $preview.experiment_sha256 `
    --accept-manual-risk

if ($LASTEXITCODE -ne 0) {
    throw "Model 2 manual activation failed; original active pointer is unchanged"
}
```

If `MANUALLY_ACTIVATED` or `ALREADY_ACTIVE` is returned, verify the actual default promoted bundle is the intended experimental SHA, and the live threshold is .63:

```powershell
@'
import json
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.live import load_default_bundle
from kalshi_edge.bootstrap.final_forecast import MINIMUM_FINAL_CONFIDENCE
from kalshi_edge.bootstrap.model_identity import current_model_identity
root = BootstrapSettings().bootstrap_dir
bundle = load_default_bundle(root)
print(json.dumps({
    "active_model": current_model_identity(root),
    "active_source_experiment_sha256": bundle.source_experiment_sha256,
    "promotion_rule": bundle.promotion_rule,
    "live_final_confidence": MINIMUM_FINAL_CONFIDENCE,
}, indent=2))
'@ | & $py -
```

Restart KalshiEdge to load the new active bundle. Do not re-arm AutoTrade without confirming the loaded hash and confidence floor match the intended policy. The previous model's hash-sealed bundle and pointer backup remain available for rollback with the existing `manual_promotion rollback --expected-active-hash ... --accept-manual-risk` command.

**Important:** No GitHub CI test can activate a model or run live inference on the user's laptop. The user must perform the local activation and verify output. Confidence 63% is model directional strength, **not** a promise of 63% or 90% future accuracy or profitable fills.
