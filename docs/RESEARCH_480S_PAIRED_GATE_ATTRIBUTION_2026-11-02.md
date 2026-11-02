# Eight-minute specialist: paired 69% gate overlap (research only)

## Why this audit is necessary

The same 2,900 historical validation markets yielded 949 qualifying specialist
forecasts (863 correct; 90.94% accuracy) versus 738 Kalshi benchmark forecasts
(672 correct; 91.06% accuracy). The specialist has **211 net extra**
high-confidence signals and better overall accuracy, Brier and log loss;
however, **its selected accuracy is slightly worse**, so the joint
higher-accuracy-and-fewer-SKIPs objective has **not** been met against the
historical Kalshi benchmark. This is not a comparison with the live incumbent.

The 69% threshold is KalshiEdge's directional confidence
`2*abs(P(YES)-0.5)`, not a promise of a 69% hit rate.
Keep this threshold fixed. More qualifying signals can increase losses if
their incremental predictions are unreliable.

## New paired diagnostic

`candidate_vs_kalshi_69_overlap` partitions the **identical** validated
480±1-second market observations into four disjoint groups:

- `both_qualified`: both exceed 69%; report each side's correct counts.
- `candidate_only`: additional specialist-qualified markets and accuracy.
- `baseline_only`: Kalshi-qualified markets the specialist skipped.
- `neither_qualified`: neither side qualifies.

The signed `net_new_qualifying_forecasts` is candidate-only minus
baseline-only. `net_new_correct_qualifying_forecasts` counts the difference
between correctly predicted exclusive qualifying cohorts, **not** total
correct predictions, confirmed trades, or profit. Missing cohort denominators
produce `null` accuracy, never an artificial 0% or 100%.

`joint_objective_against_kalshi_retrospectively` requires all of:
better overall accuracy, lower Brier and log loss, strictly more qualified
markets, and at least as accurate a selected cohort. It is a **historical
research description only**, not authorization to deploy or change risk.

## Run on local verified dataset

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$r = & .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.specialist_480 | ConvertFrom-Json

$r | Select-Object status, fit_markets, validation_markets, joint_objective_against_kalshi_retrospectively | Format-List
$r.candidate_vs_kalshi_69_overlap | Format-List
$r.candidate_69_gate | Select-Object eligible_markets, accuracy, coverage | Format-List
$r.kalshi_69_gate | Select-Object eligible_markets, accuracy, coverage | Format-List
```

The evaluation remains strictly chronological development → calibration,
before the existing reserved lockbox. This feature does not fit new weights,
search thresholds, save an artifact, access API credentials, promote models,
alter live forecasting, change AutoTrade, or place orders. Earlier inspected
historical calibration is **not** a fresh independent holdout.

The Kalshi benchmark comes from the last completed historical candle (up to
120 seconds old), not a contemporaneous executable order-book ask. Even a
retrospective joint-positive result would require a frozen specialist, new
prospective market forecasts paired with incumbent predictions, and
execution-price/fee/fill-aware net outcomes before deployment.
