# TimesFM 3.0 — Chronological KalshiEdge full-pipeline backtest

## What this tests

A **3-fold / up to 768-market** matched, pre-registered historical comparison:

- For every fold, refit the REAL KalshiEdge full historical prediction
  architecture from scratch, including original historical classifier
  selection, structural model, residual correction, ablations, stacking,
  and independent later-block calibration.
- Training uses only strictly earlier market groups. Calibration comes next.
  One whole market is embargoed. Evaluation is on subsequent unseen markets,
  then the origin advances (expanding historical train).
- TimesFM3 remains frozen zero-shot. Each candidate uses only causally complete
  Binance BTCUSDT context ending five seconds before the 480s checkpoint,
  predicting 97 five-second steps to the market settlement timestamp.
- Every market is scored against official verified Kalshi YES/NO settlement.
  The comparison includes price persistence, paired McNemar test, and
  paired bootstrap 95% accuracy-difference CI by whole market date.
- The **current unused 1,222-market evaluation lockbox is not opened**.
  All training, calibration and test rows for this research come exclusively
  from the already-existing historical development window, selected by
  timestamps and group IDs before anyone looks at outcomes.

**Critical:** The historic KalshiEdge pipeline is refitted for each fold.
This is NOT the existing live Model #1 fitted weights, which would be
in-sample on old markets. The observed performance estimates how the
same model pipeline might behave when retrained chronologically, NOT
the precise deployable accuracy of current Model #1.

## License

The official TimesFM 3.0 downloadable pretrained weights have the
TimesFM Non-Commercial License v1.0. Do not use those self-hosted weights
or output to make real-money trades or otherwise for commercial/production
purposes without a separate license granting those rights. This benchmark
is research-only, requires explicit acknowledgement, cannot promote models,
and never changes KalshiEdge Trade or AutoTrade.

- https://huggingface.co/google/timesfm-3.0-pytorch
- https://github.com/google-research/timesfm

## Run on Windows

Close KalshiEdge while running the experiment so memory/GPU use and file
revisions remain stable. Existing CUDA setup is verified for RTX 4050.

Run in PowerShell:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
$env:KALSHI_TRAIN_GPU = "auto"

& $py -m kalshi_edge.bootstrap.cuda_training --check

# 3 expanding folds with 1200 initial historical train markets,
# 200 later calibration markets, 1 embargo market,
# and 256 completely held-out test markets per fold = 768 candidates.
& $py ".\scripts\research\timesfm3_walkforward.py" prepare

if ($LASTEXITCODE -ne 0) { throw "Walk-forward model preparation failed" }
~~~

This refits KalshiEdge's **complete** baseline model in each fold and saves
research-only local results. Structural simulations and model fitting may be
computationally expensive. The printed training operations count is factual,
not a time estimate. Files stay inside:

data/bootstrap/training_runs/research/timesfm3_walkforward/

Each completed fold is durably saved and reused on rerunning the SAME
preparation command with identical data, code, and settings. The actual
historical source data are on your PC, not GitHub; CI cannot score them.

If RAM/GPU is insufficient, a smaller **different research design**
can be selected explicitly using --train-markets, --calibration-markets,
--test-markets or --folds. A changed design should use a different
--out directory to prevent mixing reports. Do not keep changing sizes
to find the best historical result: pre-register one design and report it.

## TimesFM3 inference

Your prior .venv-timesfm3 environment is separate from KalshiEdge. Only
run if you have verified that your research use of downloaded weights is
allowed by the license:

~~~powershell
$tfpy = ".\.venv-timesfm3\Scripts\python.exe"
& $tfpy ".\scripts\research\timesfm3_walkforward.py" infer --batch-size 1 --accept-noncommercial-license
if ($LASTEXITCODE -ne 0) { throw "TimesFM3 inference failed" }

& $py ".\scripts\research\timesfm3_walkforward.py" score
~~~

The saved score is:

data/bootstrap/training_runs/research/timesfm3_walkforward/score.json

## Interpretation and limitations

- A comparison is valid for **chronologically refitted KalshiEdge model**
  against frozen TimesFM3 point-price predictions on those matched markets.
  It is not a fair replay of the actual current Model #1 weights.
- TimesFM3 training-series overlap and publication cutoff cannot be fully
  verified for the exact historical BTCUSDT price windows. This restricts
  conclusions about its truly unseen zero-shot performance.
- BRTI Kalshi reference price differs from Binance BTCUSDT. The settlement
  classification may flip near the market strike even with a plausible forecast.
- TimesFM3 point forecasts do not provide calibrated Kalshi probability;
  therefore Brier, log loss, and the 69% confidence gate are reported
  only for KalshiEdge. Do not compare incomparable conditional accuracies.
- Markets on neighboring days can be correlated; day-block bootstrap partially
  accounts for this while McNemar's exact market-pair test assumes independent
  decisions. Neither test repairs training-data leakage.
- Prices, bid/ask execution, actual fills, Kalshi fees and realized P&L are not
  estimated. Nothing is deployed or traded and no result auto-promotes a model.
- These research windows were already available at the time of experiment
  design. A truly new pre-registered prospective comparison remains the final
  evidence necessary before making trading changes.
