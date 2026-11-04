# Prospective feature evidence at 480 seconds (research only)

## What changed and why

The existing live BTC15m incumbent correctly forecast 53/55 issued
directions across 153 settled markets, conditional accuracy ~96.36%,
coverage ~35.95%. The frozen specialist's 18 additional matched
qualifying forecasts were only 11 right / 7 wrong. We have **not**
found any model with demonstrably higher conditional accuracy and
coverage simultaneously.

This upgrade establishes the missing *prospective research evidence*
needed to design a genuinely better candidate. It does not replace
the live predictor or relax its confidence cutoff.

## How capture works

- After the authoritative 480-second final decision is written to
  `final_forecasts/`, the live runner attempts to capture a
  `LiveFeatureState.feature_row()` with the exact original prediction
  timestamp. The event is after the final lock and cannot change it.
- A create-once, SHA-256-protected JSON record is stored under:
  `research/480_forward_features/<UTC market-close date>/<ticker>.json`.
  It includes point-in-time numeric features, maximum source timestamps,
  immutable original final-record file digest and model identity.
- **No label is recorded.** The live feature builder historically uses
  placeholder `FeatureRow.label_yes=0` before the true outcome exists;
  this placeholder is intentionally removed from every new snapshot.
- If the live state is unhealthy, unavailable or cannot produce a
  causal feature vector, an explicit `FEATURES_UNAVAILABLE` record is
  written rather than creating fake zero feature values. Invalid or
  future timestamps cause capture to fail closed and log a diagnostic.
- Saved feature evidence never overwrites or retroactively edits older
  snapshots. The existing live model, AutoTrade, 69% confidence gate and
  optional frozen-specialist shadow are unchanged.

## PowerShell commands

Run KalshiEdge normally to capture **new future** markets. To inspect
the immutable evidence and verified settlement labels, execute:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.forward_feature_evidence_480 |
    ConvertFrom-Json

"FORWARD FEATURE CAPTURE"
$r | Select-Object status, total_480_final_records, settled_480s, pending_outcomes, valid_snapshots, missing_snapshot_records, invalid_snapshot_records, snapshots_without_features, verified_feature_examples | Format-List

"ACTUAL FORECAST ERRORS"
$r | Select-Object issued_correct, issued_incorrect, confidence_skips, confidence_skip_counterfactual_correct, confidence_skip_counterfactual_wrong, other_skips, issued_accuracy, issued_coverage | Format-List

"RESEARCH SAFETY"
$r | Select-Object recording_policy, label_source, model_trained, ready_to_promote, threshold_changed, live_trading_changed | Format-List
```

The existing verification routines may supply valid supervised
`FeatureRow` objects to *future* model-building code only by joining
a saved hash/identity-validated snapshot with the independently
verified official terminal Kalshi settlement. Snapshot values
are never reconstructed from settled-market information.

## Important limitations

The previous ~153 settled final markets predate this feature evidence
capture. They will naturally appear as missing snapshots. **Do not
backfill snapshots by recreating a historical snapshot after outcome
is known**, and do not count old missing snapshots as new challenger
SKIPs. The matched newer sample will initially be small.

This pipeline is data collection and audit infrastructure, not a new
model, not a prediction, and not an established higher-accuracy
AutoTrade strategy. Outcomes used to train a future model must be
chronologically separated from all subsequent immutable prospective
validation. Analysis of the same cohort repeatedly invalidates claims
of an independent gain. Fee-adjusted executable trading edge also
requires actual bid/ask liquidity and fill evidence.
