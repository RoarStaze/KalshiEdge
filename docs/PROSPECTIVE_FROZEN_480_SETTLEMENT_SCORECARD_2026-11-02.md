# Live frozen 480s specialist — official settlement scorecard

This adds a **read-only** local command that compares the frozen specialist to
the currently deployed 480s final forecast using verified, already-saved
official Kalshi settlements. It never fetches odds, imports exchange keys,
places trades, trains a new model, or modifies existing decisions.

## Run

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.prospective_shadow_480 | ConvertFrom-Json

"PROSPECTIVE REPORT"
$r | Select-Object status, final_480_after_freeze, settled_market_count, pending_settlements, missing_shadow_records, invalid_shadow_records, invalid_settlements, paired_probability_markets | Format-List

"EXISTING AI"
$r.incumbent | Select-Object qualified, correct, skipped, coverage, qualified_accuracy | Format-List

"FROZEN SPECIALIST"
$r.specialist | Select-Object qualified, correct, skipped, coverage, qualified_accuracy | Format-List

"MATCHED PROPER SCORES"
$r.paired_probability_metrics | Select-Object brier_improvement, log_loss_improvement | Format-List
```

Run again after additional markets settle, without deleting the frozen model.
The existing locally running history/sync process must have saved verified
`data/bootstrap/final_outcomes/...` files. This report never retrieves
missing official outcomes from the web: until they are saved, those markets
appear as `pending_settlements`.

## Counting rules

- **Denominator:** all valid saved 480s final forecasts whose *scheduled*
  checkpoint is strictly later than the original frozen timestamp AND whose
  official settlement has been verified.
- **Incumbent coverage:** actual immutable ABOVE/BELOW final decisions.
- **Specialist coverage:** hash-verified, time-matched frozen predictions with
  at least 69% model confidence. `status=OK` only means a numeric probability
  was available; it does **not** mean the threshold was reached.
- **Missing/invalid specialist snapshot:** counted as a specialist skip in
  the settled denominator, with a separate reason/integrity counter.
- **Invalid/provisional/pending official settlement:** excluded from win
  rates and reported separately; never guessed or silently labeled as a loss.
- **Proper scores:** Brier and log loss use available probabilities; their
  direct model-to-model improvements use only the **fully matched probability**
  subset to prevent comparing different market populations.
- **Gate cohort breakdown:** a separate threshold-based diagnostic restricted
  to the fully matched probability subset, not a count of actually executed
  trades.

For the initial two live examples, 0.88 P(ABOVE) passes the 69% confidence
gate but 0.79 does not; 0.26 and 0.27 P(ABOVE) each fail it. Two examples
provide no reliable estimate of profitability or out-of-sample accuracy.

**No model promotion is performed, even if the report eventually contains
80 or more settled markets.** Future evaluations still need uncertainty
estimates, robust multiple-week comparisons and execution costs, fees and
liquidity to establish an actionable advantage.
