# Train readiness and verified prospective 480-second challenger — 2026-11-10

## User-visible problem and confirmed cause

The Train page can show **40%** with **472/20 new historical feature markets**, **0/12 eligible mistakes**, and **0/2 settlement outcome classes**. This is **not a training process stalled at 40%**. The original four-round training progress is a weighted *prerequisite indicator*: 40% new future parquet groups, 40% verified mistake/correct cases in the permitted development window, 20% two settlement label classes. With 0 cases and 0 classes, the independent new-market group contribution saturates at 40% and can no longer move.

An imported old market from historical Kalshi/Binance backfill is not a market the existing live model necessarily forecast at the immutable 480-second checkpoint. It must not be enrolled as a model mistake by reconstructing its forecast *after the outcome is known*. The existing four-round training protects this provenance and chronological evaluation rule.

## Implementation

1. The old cycle stays intact. The reason text now explains when verified, officially settled mistake cases are not present in the eligible development feature window. Training remains disabled until actual prerequisites exist; progress never pretends an unavailable model was fitted.
2. **Train -> Prospective 480s model** displays a separate readiness based only on immutable feature snapshots captured at the 480-second live checkpoint, after the official final was saved, then joined with independently verified official Kalshi settlements.
3. At least **120 independent, eligible, two-class, settled markets** are required: at least 80 earlier fitting markets and 40 later chronologically distinct validation markets. No pre-checkpoint observations, missing book, future-source timestamps, missing outcome, or duplicated market IDs count. Eligibility uses the authoritative [480s, 478s] late-lock range, which matches the existing immutable final gate.
4. **Evaluate 480s challenger** (same-origin manual button) fits one **predeclared, six-feature regularized logistic specialist** on earlier, independently labeled markets. It evaluates a different, later cohort using the saved incumbent model's original checkpoint probabilities/decisions. The same 69% directional threshold and active coin-flip veto apply to candidate gate comparisons.
5. Reports **Brier, log-loss, qualifying coverage, and conditional accuracy** for the candidate against the incumbent, on the **same matched set of later markets**. No parameter or threshold search on validation outcomes, no claim of executable P/L from an offline model score.
6. Candidate is **NOT promoted into live prediction**, is not used by AutoTrade, and does not raise stakes, lower the 69% gate, disable coin-flip protection or submit any orders. Chronological holdout improvement is not yet sufficient for real-money promotion; a future, untouched post-freeze live cohort and execution validation are still required.

## Use in Windows PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# What immutable 480s data is actually available and officially settled?
& $py -m kalshi_edge.bootstrap.forward_feature_evidence_480

# Fit one fixed challenger offline only when the independent 480s cohort qualifies:
& $py -m kalshi_edge.bootstrap.forward_learning_480
```

The Train page also reports independent forward readiness automatically, and offers the manually triggered candidate evaluation once ready.

## Verification and limitations

- Tests: `tests/bootstrap/test_forward_learning_480.py`, `test_specialist_480.py`, `test_frozen_specialist_480.py`.
- Real-world improvements require *new* prospective locked forecasts with market outcomes and (for profit) authenticated fills and fees. A user-visible accuracy percentage cannot be raised by simply reducing the confidence threshold or ignoring verified SKIPs.
- The legacy historical features.parquet training round may remain at 40% while the independent forward model path gathers valid 480s examples. Both are displayed separately so the cause is explicit.
- No new trading paths or historical `UNKNOWN` fill reconciliation are authorized here.
