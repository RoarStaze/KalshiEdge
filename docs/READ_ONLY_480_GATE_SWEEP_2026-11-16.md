# Read-only 480-second probability gate sweep (November 16, 2026)

## Purpose

Measure accuracy and coverage at **eight predeclared predicted-direction probability thresholds: 63%, 68%, 70%, 73%, 75%, 78%, 80% and 82%**.

The script reuses the exact same historical sample filtering as the existing `forecast_policy_audit_480` command. The current-model **hash** is selected by default. Only existing, immutable, issued **480-second final forecasts** paired with **verified official Kalshi settlements** are eligible.

This is a **decision-rule / coverage audit**, not a new trained model, a candidate model, a recalibration study, a live setting change, a trading simulation, or validation on independent fresh data.

## Windows PowerShell

First save any uncommitted work and stop/disarm any active AutoTrade session before applying code updates:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Human-readable eight-threshold table.
& $py -m kalshi_edge.bootstrap.forecast_policy_audit_480 --sweep

# Optional structured JSON with all counts, audit exclusions and caveats.
& $py -m kalshi_edge.bootstrap.forecast_policy_audit_480 --sweep --json

# Verify calculations on synthetic checkpoint fixtures (no exchange orders).
& $py -m pytest -q tests/bootstrap/test_forecast_policy_audit_480.py
```

If the active model hash differs from the hash used for the saved historical records you want to inspect, explicitly pass the *already known* historical model hash:

```powershell
& $py -m kalshi_edge.bootstrap.forecast_policy_audit_480 --sweep --model-hash 5c249cd522d23b77db85670b21adc3b516e052278510b2f556b4764679c22657
```

`--all-models` is available for descriptive exploration but **mixes different fitted predictor versions and must not be used as a fair current-model estimate**.

The original `python -m kalshi_edge.bootstrap.forecast_policy_audit_480` JSON report remains unchanged when `--sweep` is omitted.

## Output semantics and mathematical definitions

Each threshold `t` qualifies an otherwise valid forecast only if **`max(p_yes, p_no) > t / 100`**, strictly excluding exact ties. It also retains the recorded, causal **BRTI near-strike coin-flip risk veto** (HIGH assessment or recorded normalized distance at/below the existing 0.20 cutoff).

For the same fixed settled-market denominator `N` at all thresholds:

- `qualified`: valid, settled, not flip-vetoed, and probability strictly greater than the threshold
- `skipped = N - qualified`: includes all flip-vetoed otherwise-valid sample rows, not just probability SKIPs
- `correct`: qualified forecast direction matches official Kalshi result
- `incorrect = qualified - correct`
- `accuracy = correct / qualified`, or `null` if no qualified forecasts
- `coverage = qualified / N`
- `incremental_qualified`: **newly qualifying forecasts when lowering from the next higher listed threshold**, e.g. 82% down to 80% corresponds to the probability band (80%, 82%]
- `incremental_accuracy`: **correct / newly qualified within that band**, or `null` when the band is empty
- The highest threshold (82%) has **no next higher gate in this predeclared sweep**, so its incremental fields are `null` rather than being mislabeled as zero or global accuracy.

Additional integrity exclusions include another model hash, 240-second finals, missing/invalid settlement, unhealthy/non-probability SKIPs, invalid/inconsistent probabilities, and unsettled markets.

The past model Brier score and probabilities remain **unchanged** for every threshold. The experiment **does not improve calibration** and explicitly cannot prove profitability, because there is no execution/fee/P&L comparison.

## Interpretation warning

Comparing eight thresholds on the same history is exploratory **threshold selection on the observed sample**. Reporting the largest historical accuracy/coverage pair as a guaranteed improvement would be selection bias. Keep the live gate unchanged, register a potential threshold and verify it prospectively on new settlement events before considering any AutoTrade policy changes.

Do **not** feed these audited outcomes into any new fit or use the same data as independent validation for a subsequently chosen threshold. No training, API authentication, trading commands or file writes are used by the sweep.
