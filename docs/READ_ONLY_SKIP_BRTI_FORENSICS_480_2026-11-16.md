# KXBTC15M false-SKIP and BRTI reversal forensics (2026-11-16)

## Why this is the next useful step

Recent user-provided comparisons of the currently promoted **480-second** BTC predictor on approximately 120 saved settled markets showed:

- Existing ML model beats the fixed, uncalibrated 75/25 model-plus-signals blend in **Brier score, log loss, and ECE**.
- Fixed flow/candle/BRTI signal-conflict ranking worsens same-coverage directional accuracy across the eight tested gates.
- **13** markets were flagged by the saved BRTI near-strike veto in a recent paired analysis, but the effect on *potentially accurate skipped model directions* was not reported separately.
- The existing BRTI alignment diagnostic only admitted `CONFIDENCE_BELOW_69_PERCENT` as an otherwise healthy SKIP reason; this **omitted current >63%-directional-probability SKIPs** and BRTI veto SKIPs, obscuring the relevant false-SKIP and cross-reference evidence.

These are diagnostics, not demonstrated improvements in live forecasting. More indicators, untrained votes or larger displayed percentages cannot guarantee simultaneous improvements in accuracy and coverage.

## Actual change

This PR repairs that outdated BRTI alignment reason filter and adds a **read-only** checkpoint-specific forensic audit:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Highest value: actual vs counterfactual model-direction outcomes by skip reason:
& $py -m kalshi_edge.bootstrap.skip_reversal_forensics_480

# Structured report: each skip reason, BRTI risk bin, and cross-reference subset:
& $py -m kalshi_edge.bootstrap.skip_reversal_forensics_480 --json

# Legacy alignment audit, now including healthy current 63%-gate and flip SKIPs:
& $py -m kalshi_edge.bootstrap.brti_alignment_audit

# Regression suite:
& $py -m pytest -q tests/bootstrap/test_skip_reversal_forensics_480.py tests/bootstrap/test_brti_alignment_audit.py
```

Preserve any local modifications before pulling (especially the generated
`src/kalshi_edge.egg-info/*` files and untracked backup files or virtual
environments). Do not run destructive `git reset --hard` or `git clean`.

If the active model changes, isolate the previously studied model by its exact hash:

```powershell
& $py -m kalshi_edge.bootstrap.skip_reversal_forensics_480 --model-hash 5c249cd522d23b77db85670b21adc3b516e052278510b2f556b4764679c22657
```

The script contains no network access, model fitting, trading, or writes.

## Interpretation

The report shows four *nonoverlapping* sources of error:

- **Issued**: official correct/wrong for actual, final ABOVE/BELOW calls.
- **PROBABILITY SKIPS**: valid checkpoint model predictions that the historical gate marked SKIP. A *counterfactual* correct direction means the model's saved `p_yes` happened to match the outcome **even though no forecast was issued**.
- **BRTI FLIP VETO**: final SKIPs specifically vetoed by recorded contemporaneous `risk_z <= 0.20`. A high retrospective correct fraction could suggest an overrestrictive *heuristic*, but cannot prove safe removal.
- **Other unhealthy SKIPs / exclusions**: stale/missing predictions, wrong model hashes, 240s records, missing official settlements, inconsistent probabilities, and invalid veto evidence. They must **never** be counted as immediately tradable rescued forecasts.

The risk-stratified table describes how often the saved model direction ultimately matched settlement within the BRTI volatility-adjusted distance bins: `z<=.20`, `.20<z<=.50`, `.50<z<=1.00`, `z>1.00`, and missing. It does not call `z` a calibrated flip probability.

### Independent settlement reference evidence

For each verified 480-second forecast where an immutable digest-checked forward snapshot includes synchronized BRTI and Binance data, the report checks whether **BRTI and Binance fall on opposite sides of the recorded strike** and whether the original model was wrong.

This matters because Kalshi's 15-minute price-change contracts compare the **CF Benchmarks minute-averaged reference index** at the beginning and end of the window, not a single Binance spot print. Only the specific market contract terms control. See [Kalshi crypto market rules](https://help.kalshi.com/en/articles/13823838-crypto-markets) and [CF Benchmarks BRTI](https://www.cfbenchmarks.com/data/indices/BRTI).

A missing/historical forward snapshot is NOT reconstructed from the later settlement. Missing reference-alignment values remain missing. The result's `verified_checkpoints` denominator can be smaller than its overall `matched_settled_with_usable_probability`.

## What would establish a genuine improvement?

A no-retraining decision-policy change could raise **coverage** by rescuing some currently skipped cases, but only if those additional cases are reliably correct after live safety checks. Raising coverage can lower qualified accuracy, as the previous eight-threshold sweep demonstrated.

Do not select a new veto cutoff, indicator weight, or confidence threshold by maximizing this same 120-market historical sample: it has already been inspected repeatedly. Any proposed single policy needs to be **frozen before evaluating new consecutive official settlements**, preserving the existing model and BRTI/AutoTrade safety checks. Higher in-sample correctness is not sufficient proof of out-of-sample gain or net profitability after fill and fee effects.

**This patch makes no change to the active model, probability values, 480s checkpoint, >63% directional gate, near-strike veto, or AutoTrade.** It fixes diagnostic coverage and provides exact evidence needed to decide which future change could rationally be investigated first.
