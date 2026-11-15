# Read-only 63% historical confidence comparison

**Goal:** User wants Model 1 vs Model 2 accuracy and coverage at 63% on the same historical 1,222-market 480-second checkpoint cohort.

**Verified existing implementation:** `posthoc_confidence_comparison.py` already reproduces the original sealed 69%-confidence counts for both models and calculates historical 60%, with no writes. Its 60% threshold and JSON keys are hard-coded.

**Change:** Add optional `--threshold 0.63` (default 0.60) to the read-only CLI, forward into `run_comparison(..., threshold=...)` and `compare_probabilities(..., threshold=...)`, use consistent threshold-labelled output keys (`at_63`, `qualified_added_at_63`, `matched_63`). Preserve original no-argument 60% output exactly for prior scripts. Validate finite numeric threshold in the open interval (0, 0.69), exact whole percentage points (e.g. 0.63) for unambiguous JSON keys; no threshold tuning, no writes.

**Integrity:** Must first reproduce saved 69% counts exactly, stop on any mismatch. This is post-hoc diagnostic on already-inspected historical data and not a fresh predictive validation, not a trading-threshold change, not an automatic promotion adjustment.

**Execution:** No local device access through GitHub; verify synthetic cases in CI, merge via PR on green, then give single PowerShell command for user to run against saved historical models.
