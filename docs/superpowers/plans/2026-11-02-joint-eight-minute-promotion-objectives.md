# Joint eight-minute forecast accuracy / confidence coverage promotion gate

**Goal:** A future volatility model can only replace the incumbent when the genuinely fresh, same-market eight-minute lockbox demonstrates both **higher overall directional accuracy and fewer confidence-based SKIPs**, without degrading accuracy among qualifying predictions, Brier, log loss, or calibration.

## Problem
The 480s proper-score safeguard added in PR #74 compares Brier/log loss/overall accuracy with the incumbent and Kalshi, but it does **not** measure the **unchanged 69% confidence gate's coverage and hit-rate**. A candidate could become excessively conservative, produce fewer qualified forecasts, and still pass the existing score guard.

## Design
- Add backward-compatible `GateSnapshot` to evaluation reports, capturing same-market count, number crossing `confidence=2*abs(p_yes-0.5)>=0.69`, correct qualified predictions, selected accuracy and coverage.
- Compute gate snapshots on the **same untouched 480s lockbox rows** already used by the 480s probability scores. No fresh API call, no change to the 480s ensemble, order/stake/threshold, or early-to-final schedule.
- For future volatility candidates, require:
  - no worse Brier/log loss and improved proper score against current incumbent and market (existing policy);
  - higher overall direction accuracy than the current incumbent at 480s;
  - strictly MORE 69%-qualified 480s forecasts than incumbent;
  - qualified hit-rate no worse than incumbent; none is not treated as proof of 100%;
  - sufficient 80+ distinct 480s lockbox markets, identical denominators for candidate and incumbent; missing/inconsistent gate snapshots fail closed.
- Existing baseline and previously settled 240s reports should deserialize unchanged; do not retrospectively falsify historical forecasts or alter the current promoted model.

## Implementation steps
1. Add red synthetic tests for 69% gate math, fewer SKIPs, selected-accuracy nondegradation, strict overall accuracy improvement, zero-baseline handling, invalid probabilities and legacy compatibility.
2. Implement GateSnapshot plus deterministic scorer and promotion rules.
3. Attach actual 480s gate snapshots during the **same one-time independent lockbox evaluation**; do not re-open old lockboxes to choose thresholds.
4. Adjust existing 480s synthetic pass fixture to supply matching gate proof; keep 240s regression coverage.
5. Full green CI and prod container required before merge.

**No realized profit inference:** Confidence-qualified forecasts are not executable positions; Kalshi's varying ask, fees and fill depth remain separate entry controls. The new policy is conservative and does not prove that a trainable candidate exists that satisfies all requirements.

Research: https://scikit-learn.org/stable/modules/calibration.html
