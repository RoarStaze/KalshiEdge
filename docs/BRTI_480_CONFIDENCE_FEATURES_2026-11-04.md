# Causal BRTI reference features for eight-minute BTC confidence research

## Objective

Improve future model discrimination at the **480-second** decision point by collecting two missing settlement-reference signals, rather than inflating the displayed confidence score. A good historical win rate at the existing 69% gate does not demonstrate that higher probabilities are calibrated or that more trades will be profitable.

## Immutable capture

After the normal final forecast is already saved, the research-only forward feature snapshot optionally adds:

- `brti_distance_to_strike_bps = 10_000 * (BRTI - Kalshi strike) / strike`
- `brti_binance_gap_bps = 10_000 * (BRTI - Binance BTC) / strike`

These are meaningful because Kalshi's BTC benchmark reference is not necessarily identical to the external Binance price used by existing historical feature engineering.

Only two same-tick source observations no later than the final checkpoint and at most **5 seconds old** are eligible. BRTI/Binance source values must be positive and finite, their Binance timestamp must match the original feature row, and the independently reported basis must agree with the recomputed basis within 0.0001 bps. Invalid, late, missing or contradictory observations omit both new fields; they are never zero-filled or reconstructed after settlement.

The snapshot retains its original schema, identity linkage to the immutable final forecast, SHA-256 integrity check and verified official-settlement join. Existing snapshots are not rewritten and remain readable. A new report field `verified_brti_feature_examples` counts only settled verified feature rows that contain both new signals.

## Read-only host check

From PowerShell:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$r = & ".\.venv\Scripts\python.exe" -m kalshi_edge.bootstrap.forward_feature_evidence_480 | ConvertFrom-Json
$r | Select-Object status, settled_480s, verified_feature_examples, verified_brti_feature_examples, pending_outcomes, invalid_settlements, model_trained, ready_to_promote | Format-List
```

## Limits and next research gate

**This change does not train a model, change the live V5 model, adjust the 69% threshold, issue orders or increase the UI confidence.** The count starts near zero because older saved snapshots cannot be rewritten with hindsight.

Once a meaningful forward sample of *new* snapshots and official settlements exists, compare a frozen reference-aware 480s challenger against the incumbent on strictly later untouched checkpoint markets. Predeclare chronological training, screening and confirmation segments; compare paired Brier/log loss, calibration gap, decision coverage, conditional accuracy, and correct forecasts per settled market. If those do not improve on independent future data, reject the candidate. Do not confuse market midpoint forecasts or historical directional hits with positive executable net value after Kalshi fees, fills and slippage.

The display's `94% confidence` represents a 97% model side probability under the existing transformation `2 * abs(p_yes - 0.5)`; it is not independently verified 94% or 97% win accuracy.
