# Eight-minute model-confidence diagnosis — no artificial probability boost

## Reason for the update
On 2026-11-02, the live strategy changed its immutable final-forecast checkpoint
from 240 to **480 seconds** (PR #69). Historic four-minute win-rate, Brier and
confidence cohorts do **not** measure eight-minute outcomes. A saved
80%-winning-side probability corresponds to only 60% on KalshiEdge's existing
directional confidence scale:

```text
confidence = 2 × abs(P(YES) - 0.5)
69% confidence ⇔ preferred side probability at least 84.5%
```

A high probability for the chosen side is not empirically verified accuracy.
Raising confidence mathematically, cherry-picking settled trades, or loosening
the order gate can increase expected losses and does not create information.

## Current sources reviewed

- `bootstrap.config.DEFAULT_CHECKPOINT_SECONDS` includes **480 AND 240**
  historically, so the feature schema supports both horizons.
- `bootstrap.live.LivePredictor.predict` feeds current
  `seconds_remaining` into the promoted candidate pipeline, but the code
  computes displayed confidence only from distance to 0.5. The current
  screenshot does not establish which component caused its P(YES).
- `bootstrap.evaluate` retains a legacy 240-second-specific volatility
  candidate criterion. Future claims about model quality at 480 seconds
  require horizon-specific, untouched prospective validation.
- `bootstrap.forward_monitor` previously reports aggregate accuracy across
  all markets; use the stratified report for active-policy claims.

## What the new read-only report measures

```powershell
cd "$HOME\Desktop\KalshiEdge"
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.checkpoint_confidence
```

It separates **240-second history** from **480-second current policy** and
reports for each horizon:
- Total locked final-record decisions, BELOW/ABOVE vs SKIP counts and explicit
  low-confidence vs feed/timing reasons (including pending markets).
- Only unique officially settled scored markets contribute to Brier, log
  loss, accuracy, ECE, confidence cohorts and Wilson bounds.
- Only valid contemporaneous YES bid/ask quotes contribute to Kalshi midpoint
  comparisons; midpoint is *not* an executable price.
- Corrupted explicit checkpoint metadata is excluded, old records lacking
  checkpoint_seconds are interpreted as **legacy 240 seconds**.
- The active 480s block carries a sample-readiness flag, with a minimum of
  100 settled *eligible* markets for descriptive statistics. This threshold
  is not a formal power study or permission to deploy model updates.

### Evaluation priorities before adjusting AI
1. Collect sufficient 480-second outcomes, including SKIPs and genuine
   same-checkpoint quotes, across multiple market regimes and calendar days.
2. Diagnose 480-second calibration: Is the 80% bucket winning meaningfully
   more than 80% on held-out future markets? If not, scaling to 84.5% or
   90% is unjustified.
3. Independently compare the promoted model vs recorded Kalshi probabilities
   and candidate BRTI-driven volatility/structural models on **same future
   480-second** rows. The Kalshi prior is a fair-score benchmark but not
   an executable purchase price.
4. If a candidate demonstrates better proper scores and risk-adjusted
   executable outcomes on a separately frozen, never-reused lockbox, only
   then consider promoting a checkpoint-specific calibration/model.
5. Diagnose skips from final confidence separately from fee/edge vetoes,
   liquidity, and no-fill IOC outcomes: they are different mechanisms.

**This change alters no running prediction, AutoTrade signal, order, fee gate,
model weight, confidence scaling, threshold or cash exposure.**

## Primary references

- Gneiting and Raftery (2007), Strictly Proper Scoring Rules:
  https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
- scikit-learn Probability Calibration: calibration should use data independent
  of the model fit; isotonic fitting on small samples overfits:
  https://scikit-learn.org/stable/modules/calibration.html
