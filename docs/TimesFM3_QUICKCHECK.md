# TimesFM 3.0 vs KalshiEdge Model #1 — Research-Only Quickcheck

This is an **offline exploratory experiment, not a replacement model**. It uses only
already-exposed, development-period markets after the incumbent's own calibration
end, **never** KalshiEdge's fresh untouched training/promotion lockbox. It predicts
the exact 480-second decision using a 5-second BTCUSDT close series whose last
completed candle is **5 seconds before** the forecast checkpoint. A 97-step
5-second TimesFM3 forecast reaches the settlement timestamp exactly.

**Important TimesFM 3.0 license:** Google's downloadable
google/timesfm-3.0-pytorch weights have a non-commercial, non-production license.
This script must only be run when your intended use genuinely qualifies under
those terms. Commercial/production uses of downloaded/self-hosted weights
are **not permitted** without separate authorization. This script is not wired
to Trade, AutoTrade, Train, or model deployment and does not train TimesFM.

Official links:
- https://github.com/google-research/timesfm
- https://huggingface.co/google/timesfm-3.0-pytorch
- https://pytorch.org/get-started/previous-versions/

## Windows commands

From the KalshiEdge project root in PowerShell, with KalshiEdge closed:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Prepare 32 markets and hash-verified BTC candles from DEVELOPMENT ONLY.
# This uses the current promoted Model #1 in the existing application venv.
& $py ".\scripts\research\timesfm3_quickcheck.py" prepare --markets 32
~~~

If the command reports **No safe out-of-incumbent-fitting historical
development cohort**, that is not an import error. Model #1 may already
have trained on every eligible historical development market. The strict
experiment cannot provide an unbiased comparison.

For a **quick but biased diagnostic only** (not proof TimesFM3 is better),
explicitly permit existing incumbent-training markets:

~~~powershell
& $py ".\scripts\research\timesfm3_quickcheck.py" prepare --markets 32 --allow-incumbent-training-overlap
~~~

This mode remains limited to existing development groups and never uses
the reserved fresh lockbox. Its preparation and score print
`sample_scope=INCUMBENT_TRAINING_OVERLAP`,
`comparison_valid_for_superiority=false`, and `winner=null`.
Model #1's apparent accuracy is **in-sample and optimistically biased**.
Do not promote TimesFM3, change live thresholds, or conclude superiority
from this quickcheck. For a fair comparison, collect a new untouched
prospective cohort or run a new pre-registered historical cross-validation
design that refits Model #1 without seeing each fold.

Only proceed if you've checked the licensing conditions and your proposed
research is permitted. To avoid changing the application's CUDA/XGBoost
environment, create a **separate** environment for TimesFM3:

~~~powershell
& $py -m venv ".venv-timesfm3"
$tfpy = ".\.venv-timesfm3\Scripts\python.exe"
& $tfpy -m pip install --upgrade pip
& $tfpy -m pip install "torch==2.11.0" --index-url "https://download.pytorch.org/whl/cu128"
& $tfpy -m pip install "timesfm[torch] @ git+https://github.com/google-research/timesfm.git@v3.0.0"

& $tfpy -c "import torch; print('PyTorch GPU:',torch.cuda.is_available()); print('GPU:',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
~~~

When PyTorch reports a CUDA GPU and **only if you are entitled to use the
model weights for non-commercial, non-production research**, explicitly run:

~~~powershell
& $tfpy ".\scripts\research\timesfm3_quickcheck.py" infer --batch-size 2 --accept-noncommercial-license

# Compare SAME markets against hash-verified current incumbent.
& $py ".\scripts\research\timesfm3_quickcheck.py" score
~~~

If the 6 GB RTX 4050 runs out of VRAM, retry inference with --batch-size 1
after closing other GPU-intensive programs. Initial model weights can be a
substantial download. No guarantee the model fits every Windows setup.

Research-only output lives at:
data/bootstrap/training_runs/research/timesfm3/prepared.json
data/bootstrap/training_runs/research/timesfm3/timesfm3.json

The normal live model, training checkpoints, balances, and trading data
are never changed by these commands.

## What the test actually answers

Reports matched 480-second directional accuracy and exact paired McNemar p
for TimesFM3 versus current hash-verified Model #1, plus a naive
"last completed Binance close versus strike" persistence control.

It **cannot** establish production superiority because:

- 32 markets are a small exploratory sample, selected from a previously
  exposed development window, not an untouched prospective holdout.
- TimesFM3 uses Binance BTCUSDT, while Kalshi settles against a distinct
  reference index. Difference in exchange pricing/basis can flip near-strike
  outcomes even when the forecast is otherwise reasonable.
- TimesFM3 produces a price forecast, not a calibrated Kalshi ABOVE/BELOW
  probability. No comparable 69% confidence-gated forecast coverage, Brier
  score, log-loss, net P&L or trading execution is claimed.
- Past market prices and training signals may have influenced prior model
  development. Do NOT use this result to choose or promote a new live model.
- TimesFM3's generic benchmark rankings do not establish superiority on
  eight-minute BTC settlements.

If preparation reports too few safe development markets, do not bypass
its safeguards by choosing the unused fresh lockbox. Use the clearly
labeled in-sample diagnostic option only when you accept that its accuracy
comparison **cannot** identify a superior deployable model.
