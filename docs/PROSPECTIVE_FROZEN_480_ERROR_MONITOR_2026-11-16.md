# Frozen prospective 480-second error validation (November 16, 2026)

## Problem fixed

Historical experiments on the same 120–129 saved BTC 15-minute markets have
already selected/inspected candidate indicators, confidence cutoffs and BRTI
conditions. Repeatedly choosing the best one on those settlements cannot
establish out-of-sample improvement. The existing
`forward_monitor.evaluate_forward_reliability` also recognized an archived
69%-strength SKIP but omitted current 63%-directional-probability and
BRTI coin-flip-veto SKIPs, silently reducing coverage of valid probability
quality diagnostics.

**This patch fixes the contemporary SKIP readout and adds a genuine
post-registration evaluation cohort without fitting a model or making trades.**

## New one-time frozen baseline

Run on your own KalshiEdge laptop from PowerShell:

```powershell
cd "$HOME\Desktop\KalshiEdge"

# Preserve existing local work before pulling; do not reset/clean it.
git status --short
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"

# Run ONCE, immediately, BEFORE the new markets you intend to evaluate:
& $py -m kalshi_edge.bootstrap.prospective_error_monitor_480 --freeze --json

# Refresh whenever new saved official settlements arrive, with no retraining:
& $py -m kalshi_edge.bootstrap.prospective_error_monitor_480

# Full machine-readable numbers:
& $py -m kalshi_edge.bootstrap.prospective_error_monitor_480 --json

# New regression tests:
& $py -m pytest -q tests/bootstrap/test_prospective_error_monitor_480.py tests/bootstrap/test_forward_monitor.py
```

**Do not pass a custom timestamp to the CLI.** The registration uses the
computer's real current UTC timestamp (nanoseconds) and does not expose a
backdating option. It creates exactly one local frozen manifest:

```text
data/bootstrap/research/prospective_monitor_480/frozen_baseline.json
```

The precise root is `BootstrapSettings().bootstrap_dir`. If your config
changes it, the manifest is stored under that configured bootstrap root.
The file includes a SHA-256 integrity check, recorded active model hash,
original 63% **predicted-side probability** gate, existing BRTI z<=0.20
near-strike veto, 480-second final checkpoint, one-time registration time,
the predeclared reference-disagreement error question, and a fixed first
**480 recorded new forecasts** evaluation horizon.

- The file is created exclusively: another `--freeze` **refuses** to
  overwrite/restart it. Changing it or its captured settings invalidates
  integrity checks. Keep this file intact to retain prospective provenance.
- `--status` (the default, no flag required) reads it without writing.
  Earlier forecasts, older model hashes, 240-second checkpoints and
  separately fitted fusion records are **not allowed into the cohort**.
- After 480 new recorded checkpoints, the sample is closed by timestamp
  order, **regardless of outcome**. A pending older market cannot be silently
  replaced with a later winning market.
- Since KalshiEdge could be offline for some markets, the denominator is
  **recorded forecast checkpoints, not all Kalshi BTC markets that existed**.
  Offline/missing rounds are not inferred or assigned outcomes.
- The model should continue saving immutable 480-second forecasts, causal
  checkpoint snapshots and verified official Kalshi settlements as before.

## What gets measured on future settlements

1. **Actual issued forecast accuracy** and operational issuance coverage,
   using all settled recorded finals in the chosen horizon (including
   unhealthy SKIPs in coverage denominator).
2. Valid confidence/probability-gated SKIPs and BRTI vetoed SKIPs, scored
   *only counterfactually*: not trades, not profit.
3. Independent saved model `p_yes` quality: Brier score, log loss, ECE and
   directional accuracy on the **same future healthy settled cohort**.
4. Error rate conditional on exact checkpoint-time reference agreement:
   `agree_with_model`, `agree_against_model`, `disagree`, and
   `unavailable`. Uses verified, timestamp-consistent BRTI/Binance
   price-distance features bound by the original forecast digest.
5. Saved BRTI strike-distance risk strata (`z<=0.20`, `0.20<z<=1`,
   `z>1`, and missing) for raw error attribution.
6. A **predeclared observational under-gate subgroup**:
   saved model probability **at or below 63%**, BRTI risk **z>1**, and
   **both BRTI and Binance agreeing with the original model's direction**.
   Report how often the model side was correct or wrong. This subgroup
   never becomes an issued trade or an adjustment to model probability.
7. Model/policy drift flags, missing/invalid causal snapshots, pending official
   settlements, invalid evidence, different model versions and skip reasons.

A risk flag, signal alignment or sample accuracy is NOT a calibrated
probability of reversing. No apparent historical or prospective gain is
automatic authorization to increase trade size, relax the coin-flip veto,
change 63% live threshold or alter AutoTrade.

**Evidence/decision rule:** until the 480-recorded-market cohort is complete,
reports are strictly descriptive interim progress. At its end, evaluate
uncertainty, effective sample size in each subgroup, calibration, Brier/log
loss, quote executable EV and **actual** fee/fill-adjusted P/L separately
before considering any new decision policy. Do not select new indicator
cutoffs after seeing those same outcomes and then call the comparison
independent validation.

## What changes in production

Only read-only monitoring and saved prospective manifest registration.
There is **no retraining, background task, live prediction pipeline change,
model promotion, new AutoTrade order, or automatic threshold change**.
Running `--freeze` and `--status` yourself does not interrupt an already
running training or web UI process.

The existing forward reliability CLI additionally includes the modern
healthy model-probability SKIPs rather than excluding them from Brier
and log-loss calculations. This corrects measurement; it is not a claimed
increase in model skill.
