# TimesFM-3 — historical 31k-market settlement adapter (research only)

This is **NOT fine-tuning Google's 330M TimesFM-3 model weights**. It trains a small, regularized, probability-calibrated settlement classifier on TimesFM-3's **frozen terminal-price forecasts** and historical market labels. This was selected because Google publishes a tested LoRA fine-tuning example for **TimesFM-2.5**, but not an equivalent supported TimesFM-3 training recipe that has been verified on the RTX 4050. There is no assumption that a bigger neural network is better.

## Legal boundary — read before using

Downloaded `google/timesfm-3.0-pytorch` weights are restricted under Google's **TimesFM Non-Commercial License v1.0**. The restrictions cover weight derivatives **and outputs**, including use for revenue-generating trading decisions, production activity and fine-tuning or distillation of commercial models. This research tool requires an explicit acknowledgement before it loads TimesFM-3. **Do not use it for KalshiEdge live Trade/AutoTrade or commercial development unless your intended use is permitted by Google or separately authorized.** Authorized Google Cloud services may have different commercial terms. If your sole objective is immediate commercial trading integration, investigate TimesFM-2.5's separately Apache-2.0-licensed weights instead.

References:
- https://github.com/google-research/timesfm
- https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE
- https://github.com/google-research/timesfm/tree/master/timesfm-forecasting/examples/finetuning

## Data and tests

The `prepare` stage reads the existing **hash-verified historical dataset** and only the `_verified_context(...).plan.development_groups`. No current calibration or independent promotion lockbox groups are deliberately enrolled. It insists on one saved 480-second observation per market, official Kalshi settlement/strike verification, and **256 consecutive 5-second Binance BTCUSDT context closes** ending 5 seconds before the 480-second forecast checkpoint. It refuses data-source failures and low coverage rather than creating synthetic market observations.

The total eligible sample may be **less than 31,157**: some records must remain in calibration and unopened independent evaluation; others may lack a complete verified 480s causal history. The number reported is actual, not a promised count.

A 70% chronological training block is followed by **two embargoed whole markets**, 15% calibration block, **two embargoed whole markets**, and final 15% development-period held-out evaluation. With fixed regularization, the adapter trains on:

- TimesFM-3 predicted terminal BTCUSDT log-distance to Kalshi strike
- Last causally completed BTCUSDT close log-distance to strike
- Predicted BTCUSDT log return
- 32-step realized BTCUSDT volatility

Training uses only earlier official settlement labels. A later sigmoid calibration fit generates probabilities for the untouched *within-development* evaluation block. Reports include accuracy, Brier, log loss, original TimesFM-3 point direction, price-persistence direction, and the same 69%-strength gate as KalshiEdge.

**Cannot establish deployable superiority:** TimesFM-3's pretraining-data overlap is not independently verified, the development period was available during earlier KalshiEdge research, and a terminal Binance price is not a Kalshi index-window settlement forecast. It does not refit the current KalshiEdge model or measure expected profit, fill prices or fees.

## Windows PowerShell (separate environments)

Close KalshiEdge for this resource-heavy *offline* research. Do not run full-model Train Now simultaneously.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main

# Existing research-enabled KalshiEdge environment.
$py = ".\.venv\Scripts\python.exe"
& $py ".\scripts\research\timesfm3_market_adapter.py" prepare
if ($LASTEXITCODE -ne 0) { throw "Preparation failed — inspect dataset or verified historical contexts" }
```

The initial prepare phase reads existing local history, but does not download or import new history. It writes `data/bootstrap/training_runs/research/timesfm3_market_adapter/prepared.json`.

**Only if your purpose genuinely qualifies under the non-commercial license** and your separate TimesFM3 CUDA environment has been installed according to `docs/TimesFM3_QUICKCHECK.md`:

```powershell
$tfpy = ".\.venv-timesfm3\Scripts\python.exe"
& $tfpy ".\scripts\research\timesfm3_market_adapter.py" infer --batch-size 1 --accept-noncommercial-license
if ($LASTEXITCODE -ne 0) { throw "TimesFM3 inference failed" }

# Supervised adapter fit and chronological research report. This uses the app's
# sklearn environment and does NOT fine-tune TimesFM-3 model weights.
& $py ".\scripts\research\timesfm3_market_adapter.py" fit
if ($LASTEXITCODE -ne 0) { throw "Research adapter training failed" }
```

Inference writes an append-only, source-hash-bound `timesfm3.jsonl` journal and **resumes complete batches** on rerun, verifying every saved forecast against the same ticker order. No saved forecast is silently replaced. Recreate only from scratch if intentionally starting a *new* experiment with different data or settings. Do not edit the source hash or attempt to use training artifacts in live code.

The final report is `data/bootstrap/training_runs/research/timesfm3_market_adapter/adapter_report.json`. It stores coefficients and evaluation statistics **only in this ignored research area**. The script does not import live trading modules, alter balances or create promoted model bundles.

## What can be verified in GitHub CI

CI exercises data-scope selection, chronology/embargo, feature causality, matching source hashes, numerical fitting, and the mandatory license gate with synthetic observations. The actual 31k-market results, GPU inference, time-to-finish and peak RTX4050 VRAM **cannot** be verified without the historical archive and CUDA GPU on the user's laptop. Those numerical outcomes must be obtained from the local commands above; do not present fabricated gains.
