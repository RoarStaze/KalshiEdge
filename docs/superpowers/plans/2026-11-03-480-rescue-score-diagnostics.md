# 480s development rescue-score diagnostics — implementation plan

## Problem (November 3, 2026)

Research-only precision guard rejected all thresholds on the 3,244-market
development selection cohort. At fixed .875 estimated correctness 16
original skips qualified, 15 correct / 1 wrong. At >=.90, zero qualified.
Original 69%-confidence forecasts were 920/991 right (92.84%).
The .875 sample was below the required 40 and the uncertainty bound was
not enough to demonstrate superiority.

## Decision

Add **observability**, NOT another retrospectively chosen threshold. The
model's predicted-correctness score distribution and its ranking/discrimination
on the *development threshold-selection fold only* must be visible without
unlocking internal-confirmation or calibration labels.

## Files

1. `tests/bootstrap/test_rescue_score_diagnostics_480.py`: first red
   CI must fail because diagnostic module is missing; test fixed score bins,
   correct and wrong directions, top-K tie handling, ROC AUC one-class
   behavior, NaN rejection and unchanged existing threshold selector.
2. `src/kalshi_edge/bootstrap/rescue_score_diagnostics_480.py`: one pure
   `describe_rescue_scores(labels,p_direction,p_correctness)` returning:
   - fixed nonoverlapping correctness-probability bins (including the .875/.9 break)
   - per-bin n, direction correct, wrong, observed rate, mean predicted score,
     Wilson 95% bounds
   - predeclared top-K n=20,40,100,250 by descending estimated correctness,
     95% Wilson and all errors
   - ROC-AUC discrimination of correctness across original direction-confidence
     SKIPs when both classes exist, Brier and quantiles
   - separation from original issued qualifying predictions.
3. `src/kalshi_edge/bootstrap/precision_guarded_480.py`: call the pure
   function within existing `choose_guarded_threshold` and add one
   `score_diagnostics` field. Preserve all original fixed grid and
   acceptance decisions byte-for-byte.
4. `docs/RESCUE_SCORE_DIAGNOSTICS_480_2026-11-03.md`: operator PowerShell.

## Safeguards / verification

- Only already used **third OOF development selection** labels;
  never read or analyze fourth internal confirmation or 2,900 calibrated
  labels when no safe threshold is selected.
- No new model, thresholds, trading instructions, persisted output,
  or exchange access; no user data upload.
- Top-K curves and bin rates are **descriptive only**; any policy chosen
  *because of this report* needs independent future evaluation.
- Confirm test-first red then all CI tests and production container green
  on PR head before any merge.
