# Eight-minute causal OOF reliability-gate experiment

## Objective

Test **both fewer SKIPs and higher conditional hit rate** on the same
eligible 480-second BTC15m development/calibration historical rows, with
one report showing correct forecasts *per settled market*.

This is a new, **read-only research experiment**, NOT the deployed V5
model, the previously frozen specialist, a live 8-minute score, or a
recommendation to buy Kalshi contracts.

## Method (fixed before looking at calibration results)

1. Load the manifest/SHA-verified historical `derived/features.parquet`,
   honor the predeclared `development`, `calibration`, and untouched
   `lockbox` partitions. Strictly select unique, healthy 480-second
   observations, causal BTC observations, and two-sided timestamped quotes.
2. Fit a fixed six-feature logistic direction estimator
   (`C=0.15`) on earlier development rows.
3. Build **four expanding-window out-of-fold direction forecasts** on
   later development rows. Each fold trains on strictly earlier rows;
   each correctness training target equals whether the out-of-fold
   predicted direction ultimately matched its settlement. The model
   estimating correctness never sees its own training row's in-sample
   direction estimate.
4. Train the fixed nine-input correctness estimator (`C=0.10`) on those
   OOF successes and failures. Every input comes from the checkpoint:
   predicted confidence, Kalshi midpoint confidence and direction
   agreement, strike distance, 60/300s volatility, 60s momentum,
   spread and quote staleness.
5. Refit direction only on all development markets. On strictly later
   calibration rows score *three predeclared rules*, no threshold search:
   - `baseline_69`: direction confidence at least 69%.
   - `reliability_85`: estimated correctness at least 85%.
   - `rescue_union`: baseline OR reliability; never remove an original
     baseline decision.

## Run (PowerShell)

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.selective_reliability_480 | ConvertFrom-Json

$r | Select-Object status, direction_fit_markets, oof_correctness_training_markets, evaluation_markets, joint_objective_retrospective, meta_rescues_beyond_original | Format-List

"ORIGINAL 69%-GATED LOGISTIC"
$r.baseline_69 | Format-List

"85% ESTIMATED CORRECTNESS GATE"
$r.reliability_85 | Format-List

"BASELINE + SELECTIVE RESCUE"
$r.rescue_union | Format-List

"OUT-OF-FOLD CHRONOLOGY"
$r.oof_folds | Format-Table -AutoSize
```

## Interpretation

Only `status=RESEARCH_CALIBRATION_ONLY` reports actual paired results.
`joint_objective_retrospective=true` requires **strictly greater**
coverage, conditional directional accuracy, and correct forecasts per
evaluated market than the 69%-gated logistic baseline. A false value
is valid and means the fixed experiment did not meet all objectives.
The union policy is separately shown; it may increase coverage at a
cost in conditional accuracy.

**Limitations:** The held-out *calibration* cohort has previously been
inspected and is not a fresh independent lockbox. This research compares
two gating rules around a historically retrained logistic direction
estimator, not a historical recreation of deployed bootstrap-hybrid-v5.
Neither a favorable retrospective result nor the 85% numerical
threshold establishes a calibrated 85% probability of success or
positive fee-adjusted expectancy.

No model artifacts are saved, no live gates or AutoTrade changed, and
`ready_to_promote` remains false. A policy selected after viewing
calibration requires a new, separately registered independent prospective
evaluation before deployment.
