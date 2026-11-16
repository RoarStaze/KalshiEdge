# Risk-conditioned BRTI / Binance error attribution (read-only)

## Purpose and interpretation

The promoted KalshiEdge KXBTC15M predictor resolves a **CF Benchmarks
BRTI-based settlement** while the market-data feature stack also uses Binance.
Previously inspected settled forecasts suggested BRTI/Binance strike-side
disagreement often coincides with mistakes. This is a plausible diagnostic,
not evidence of an independent effect: disagreement may simply be concentrated
near the strike, where the model is less certain.

This tool now **conditions on saved, checkpoint-time BRTI volatility-normalized
strike distance** and separately isolates the **current >63% predicted-side
probability gate with the original near-strike veto**. It does not train,
produce probability weights, alter saved forecasts or place trades.

## Run on the local historical evidence

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.bootstrap.reference_conditioned_480

# Machine-readable, full denominators and exclusion reasons
& $py -m kalshi_edge.bootstrap.reference_conditioned_480 --json

# Optional: isolate the currently studied model even after new promotion
& $py -m kalshi_edge.bootstrap.reference_conditioned_480 --model-hash 5c249cd522d23b77db85670b21adc3b516e052278510b2f556b4764679c22657

# Tests
& $py -m pytest -q tests/bootstrap/test_reference_conditioned_480.py
```

Generated `src/kalshi_edge.egg-info/*` files may already be modified locally.
Preserve those unrelated changes; **do not** run destructive git resets or
repository cleans. The audit is offline and read-only, requires no credentials,
and can run while training continues.

## Exact source and population

Only same-model 480-second saved finals joined to **official saved Kalshi
settlements** are considered. A forecast's model prediction and source snapshot
must be captured at the same immutable checkpoint. The snapshot must match the
forecast hash and report valid Binance and BRTI source timestamps **on or
before the forecast, no more than 5 seconds old**. The frozen 480s features
must include signed `brti_distance_to_strike_bps`,
`brti_binance_gap_bps` and `btc_close`, with the signed basis agreeing with
the strike-distance and Binance closing/reference price to **0.01 bps**.
No future/settlement price can be substituted for missing inputs.

Missing snapshots, inconsistent reference prices, stale data and unhealthy
forecasts are excluded and counted. Exactly-on-strike readings are excluded
as ambiguous; they are not counted as agreement. These requirements mean the
audit may have **fewer** examples than previous broad error reports.

### Three intentionally different cohorts

1. **All healthy historical model directions.** Includes retrospectively
   scored model directions from valid historical SKIPs; these were NOT trades.
2. **Actual issued historical finals.** Only saved ABOVE and BELOW decisions,
   irrespective of which older live policy version issued them.
3. **Current 63% live-policy replay.** On those same originally captured
   probabilities, qualify only if `max(p_yes,p_no)>0.63` and BRTI near-strike
   veto does not apply. Exact ties do NOT qualify. A historical SKIP that
   would now qualify remains hypothetical, **not** an actually issued order.

Risk bands are predeclared by the existing forensics: `z <= 0.20`,
`0.20 < z <= 0.50`, `0.50 < z <= 1.00`, `z > 1.00`, and unavailable.
In each band and cohort, compare the number **wrong / total** among
BRTI/Binance strike-side agreements and disagreements. A cell with no
observations is reported as `0/0` with a null JSON error rate, never as
0% empirically measured error.

## Baseline-versus-veto accounting (not a deployment proposal)

The audit also calculates one **hypothetical** policy: reject every current
replay-eligible market where the reference sides disagree. The report must
show **both** wrong forecasts avoided and **correct forecasts removed**,
along with coverage and qualified accuracy on an identical source denominator.
This is a diagnostic cost ledger, not recommended AutoTrade logic.

This 123-market cohort has already been examined repeatedly. It is neither
a newly registered evaluation set nor a fresh independent holdout. Sparse
within-risk-band cells, market regime drift and confounding can make the
unadjusted ~3.7x reported error-risk ratio misleading. **No new profit,
out-of-sample accuracy or safety claims are permitted** from this result.
Even an apparently superior table cannot authorize a live gate, probability
or position-sizing change. Follow with a pre-registered, chronological,
independent forward evaluation if the diagnostic remains promising.

### Safe follow-up

If the disagreement signal retains informative variation *within* well-sized
risk bands of **actually live-qualified** records, treat it only as a
candidate feature for the existing sealed `signal_fusion_480` process.
That fitter still requires at least **480 eligible same-model** records and
its separate chronological calibration/holdout acceptance checks.

## Development verification

The tests exercise integrity of saved records, strict 63% boundary,
near-strike risk veto, as-of 5-second feed freshness, arithmetically
inconsistent basis exclusion, historical SKIP versus current replay and
loss of correct forecasts under a counterfactual veto. The CLI is verified
read-only against a synthetic temporary dataset. GitHub CI exercises the
full project suite.
