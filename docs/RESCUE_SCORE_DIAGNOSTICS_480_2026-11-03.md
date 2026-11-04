# Diagnosing 480s rescued-score saturation (read-only)

The precision guard did not find a safe threshold on 3,244 later
development markets. At 87.5%, only 16 original SKIPs qualified (15
retrospectively correct / 1 wrong); above 90%, no rescues qualified.
This indicates a **score-volume limitation**, and 15/16 does NOT prove
the rescues' true accuracy exceeds 920/991 for the baseline.

This change adds scores for **only the already-used development
threshold-selection OOF fold**, without modifying any selection,
training, confirmation, model artifact, or trading logic.

## Run in PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.precision_guarded_480 | ConvertFrom-Json

"RESEARCH STATUS"
$r | Select-Object status, direction_fit_markets, selected_threshold | Format-List

$d = $r.threshold_selection.score_diagnostics

"ORIGINAL SKIPS / SCORE DISTRIBUTION"
$d | Select-Object original_skips, original_qualifying_markets_excluded, skipped_direction_correct, skipped_direction_wrong, skipped_direction_accuracy, above_90_percent, roc_auc_on_skipped_direction_correctness, brier_of_estimated_correctness_on_skips | Format-List
$d.score_range | Format-List

"FIXED SCORE BINS (NO THRESHOLD SEARCH)"
$d.bins | Select-Object min_score, max_score, markets, correct, incorrect, observed_accuracy, mean_estimated_correctness, wilson_95_lower | Format-Table -AutoSize

"PREDECLARED TOP K (RANKING ANALYSIS, NOT NEW TRADE RULES)"
$d.top_k | Select-Object requested_top_k, markets, correct, incorrect, observed_accuracy, wilson_95_lower | Format-Table -AutoSize

"UNCHANGED FIXED SAFETY GRID"
$r.threshold_selection.grid | Format-Table -AutoSize
```

## Interpretation

- The `bins` table counts each previously skipped market once in a
  predeclared estimated-correctness interval. The mean model score
  can be compared to the actual fraction of correct directions in
  that bin, but small bins remain noisy.
- `roc_auc_on_skipped_direction_correctness` measures whether model
  scores rank retrospectively correct skipped directions above incorrect
  ones (0.5 is chance ranking, 1.0 perfect ranking). It is null if
  no original SKIPs or only one outcome class exists.
- `top_k` (20,40,100,250) is a **diagnostic**, not an adaptive
  recommendation to rescue precisely that number.
- The code does NOT open fourth-fold confirmation or 2,900 previously
  inspected calibration outcomes if the development threshold fails.
  Scores are never printed for individual ticker/identities.
- A new threshold derived from the table would require independent
  later confirmation. This change never relaxes the existing
  69% live gate, retrains models, or changes AutoTrade.
