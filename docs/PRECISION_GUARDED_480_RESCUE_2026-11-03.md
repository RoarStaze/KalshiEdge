# Precision-guarded 480-second rescue experiment

## Purpose

The first OOF reliability-gate run on 2,900 historical markets generated
**130 additional qualifying forecasts**, of which 104 were right and
26 wrong (80% rescue hit rate). That raised coverage but **decreased
conditional accuracy** from 90.94% to 89.62%, so it did not meet the
requirement to increase both.

This experiment tests a *stronger, development-only* rescue rule that
must survive four chronological checks before we even look at
historical calibration outcomes.

## Fixed four-stage protocol

1. Earlier development rows train the six-feature directional model
   (logistic C=0.15). Strictly timestamp-valid 480-second observations only.
2. First two later development OOF folds train a correctness classifier
   (logistic C=0.10), using checkpoint-only features and whether each
   **out-of-fold** direction was actually correct.
3. Third development OOF fold selects at most one correctness threshold
   from the predeclared set **87.5%, 90%, 92.5%, 95%, 97.5%**. It must
   add at least 40 forecasts, and the rescued predictions' 95% Wilson
   *lower bound* must exceed the 69%-gated original's conditional hit rate.
4. Fourth development OOF fold independently confirms, unchanged,
   that the selected threshold adds at least 20 forecasts and its
   rescue Wilson lower bound exceeds baseline accuracy. If it fails,
   the experiment refuses further selection.
5. Only after these gates pass does the evaluator score the rule on the
   later 2,900-market *already inspected* historical calibration split.

**This stricter policy may produce no qualifying rule; that is a valid
result.** We will not silently lower the risk constraints to force
more predictions.

## Run on PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.precision_guarded_480 | ConvertFrom-Json

"PRECISION GUARD STATUS"
$r | Select-Object status, direction_fit_markets, oof_meta_fit_markets, threshold_selection_markets, internal_confirmation_markets, selected_threshold, evaluation_markets, joint_objective_retrospective | Format-List

"TRAINING-ONLY THRESHOLD SELECTION"
$r.threshold_selection | Select-Object status, selected_threshold, selected_rescue_correct, selected_rescue_incorrect, selected_rescue_wilson_lower | Format-List

"STRICT INTERNAL FORWARD CONFIRMATION"
$r.internal_confirmation | Select-Object passes, threshold, rescue_wilson_95_lower | Format-List

"ORIGINAL BASELINE"
$r.baseline_69 | Format-List

"GUARDED HYBRID"
$r.guarded_union | Format-List

"ADDITIONAL RESCUES: BOTH WINS AND LOSSES"
$r.additional_rescues | Format-List

"CHRONOLOGICAL OOF FOLDS"
$r.oof_folds | Format-Table -AutoSize
```

## Report statuses

- `NO_SAFE_DEVELOPMENT_THRESHOLD`: none of the fixed thresholds passed
  the training-only accuracy and uncertainty criteria. **No candidate.**
- `FAILED_INTERNAL_CONFIRMATION`: a threshold selected on block three
  failed on untouched subsequent development block four. **No candidate.**
- `RESEARCH_CALIBRATION_ONLY`: the threshold survived both development
  gates and was evaluated on historical calibration. A positive
  `joint_objective_retrospective` means both coverage and conditional
  accuracy rose **against a newly trained six-feature logistic baseline**.
- `INSUFFICIENT_*`, `LEAKAGE_OR_OVERLAPPING_PERIODS`,
  `SINGLE_CLASS_*`: insufficient or invalid training evidence.

## Safeguards and interpretation

- Does NOT reuse the 2,900 calibration labels to choose thresholds.
  Those labels have been inspected in prior experiments, so positive
  results here are **not independent validation**.
- Does NOT access reserved lockbox, promote or save any model, alter
  live original V5/frozen specialist policy, or place trades.
- OOF training and later confirmation are strictly ordered; no use of
  later settlements to select an earlier trade.
- Estimating high correctness probability is NOT proof of an 85%-95%
  forward win rate. The Wilson bound assumes independent binary trials;
  BTC rounds may be serially dependent.
- Any potential candidate must still be frozen and evaluated against
  subsequently saved official settlements, plus executable ask prices,
  liquidity, fees and slippage, before claims of live advantage.
