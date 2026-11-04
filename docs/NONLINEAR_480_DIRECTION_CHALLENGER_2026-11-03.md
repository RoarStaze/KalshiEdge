# Eight-minute nonlinear direction challenger (research only)

## Why this experiment exists

Our previous correctness classifier ranked 2,253 historically skipped
480-second predictions with ROC-AUC ~0.632. At its 87.5% estimated
correctness gate it found just 16 extra directions (15 right, 1 wrong),
and above 90% it found none. Lowering the threshold cannot establish
both higher accuracy and fewer skips.

This experiment asks whether **nonlinear outcome modeling**, rather
than a second correctness gate, can generate better point-in-time
direction probabilities.

## Models and data

- **Baseline:** fixed logistic C=.15 on six causal features.
- **Challenger:** one fixed scikit-learn histogram-gradient-boosted
  tree model (140 iterations, 9 leaves, .05 learning rate,
  min leaf 80, L2=5, early stopping disabled, random seed zero).
- **Same input features:** normalized BTC distance to strike, realized
  volatility at 60s and 300s, 60s return, Kalshi quote midpoint,
  Kalshi spread.
- Both use exact point-in-time 480-second observations from the
  hash-verified development partition. Training/validation rows
  must pass source timestamps, duplicate, momentum, and quote checks.
- Development split **chronologically 60% train, 20% screen,
  20% confirm**. Both are trained exactly once on the first 60%.
  Every adjacent period must be at least one 15-minute round apart.
- **Fixed confidence gate:** both use
  `2 × |P(ABOVE) − 0.5| >= 0.69`.
- A challenger passes a development block only if both its
  **coverage and conditional directional accuracy** strictly exceed
  logistic baseline, correct forecasts per market increase, and it
  qualifies at least 100 directions.
- The candidate must pass *both* chronologically later development
  blocks before any historical calibration metrics are calculated.
  If either fails, `DEVELOPMENT_REJECTED` is the expected defensible
  result, not a runtime failure.
- `RESEARCH_CALIBRATION_ONLY` then means the candidate additionally
  received descriptive scores on the previously inspected 2,900-case
  calibration period. That period is not independent validation.

**Do not mistake the logistic comparator for the deployed V5 AI.
Neither model changes live inference or AutoTrade.**

## PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.nonlinear_direction_480 | ConvertFrom-Json

"STUDY STATUS"
$r | Select-Object status, development_markets, fit_markets, development_screen_markets, development_confirm_markets, historical_calibration_markets, joint_objective_retrospective | Format-List

foreach ($name in @("development_screen", "development_confirm", "historical_calibration")) {
    $window = $r.$name
    if ($null -eq $window) { continue }
    "TIME-ORDERED COHORT: $name"
    $window | Select-Object markets, joint_objective_met, coverage_delta, conditional_accuracy_delta, correct_per_market_delta | Format-List

    "LOGISTIC DIRECTION"
    $window.baseline_logistic.gate_69 |
        Select-Object markets, qualified, correct, incorrect, skipped, coverage, accuracy, correct_per_market | Format-List

    "FIXED NONLINEAR DIRECTION"
    $window.candidate_nonlinear.gate_69 |
        Select-Object markets, qualified, correct, incorrect, skipped, coverage, accuracy, correct_per_market | Format-List

    "PROBABILITY QUALITY"
    $window.baseline_logistic.probability_metrics | Select-Object brier, log_loss, ece | Format-List
    $window.candidate_nonlinear.probability_metrics | Select-Object brier, log_loss, ece | Format-List
}
```

## Meaning of the result

A fixed nonlinear model might learn volatility/strike interactions
that a linear log-odds estimator cannot. This is a research hypothesis,
**not an already observed accuracy improvement**.

A strict two-window result of `DEVELOPMENT_REJECTED` means at
least one development cohort did not meet the full joint objective.
We do not adjust hyperparameters or thresholds after seeing results.

The full historical dataset has been repeatedly inspected, and none
of these retrospective comparisons establishes an independent
forward-test improvement or a profitable after-fees trading edge.
No saved weights, reserved lockbox access, live trading, threshold
adjustments, or automatic model promotion are implemented.
