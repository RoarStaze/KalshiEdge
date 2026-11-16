# No-retraining 63% directional probability rule and historical comparison

## What changes (and what does not)

1. **Current model remains intact.** No full retraining, continuation fit, newly weighted technical indicators, new ensemble, shadow model, new calibration or altered probability is created by this policy/UI patch.
2. At the immutable **480-second** final checkpoint, an otherwise healthy predicted side now qualifies **only if its displayed probability is strictly greater than 63%**. Exactly 63% or below is SKIP. Previously the internal `confidence = 2*abs(p_yes-0.5)` was compared with 63%, silently requiring 81.5% directional probability. This is a **coverage-changing decision policy**, not improved model accuracy.
3. The existing BRTI near-strike coin-flip veto, stale/invalid feed protection, late-lock protection, immutable final record, and exchange risk controls remain intact. A 78.38% directional probability can now produce an ABOVE/BELOW final call **unless another safety rule vetoes it**.
4. Live AutoTrade no longer adds an obsolete confidence-*strength* veto after a valid new-directional locked final. For older final dicts without a stored directional probability it conservatively retains the earlier confidence check; malformed directional probability fails closed. The model-strength score is still retained separately for historical reporting and fee-adjusted quote diagnostics.
5. AutoTrade history status display uses **SKIPPED** for a journal `NOT_SUBMITTED` and **BOUGHT** for `FILLED`; partial fills show **PARTIALLY BOUGHT**. The underlying journal fields and per-stage details keep the exact original status, including whether the attempted order was not submitted due to an execution failure. **SKIPPED is only a UI headline, not proof the system intentionally chose not to trade.**
6. Trade-panel grid columns are equal width, with consistent stretch/margins. Old training results keep their *historical 69% or 63% model-strength gate* as actually measured. Their archived numbers are not relabeled as new 63% probability-gate results.

## Read-only command: matched historic policy comparison

The following command reads your existing immutable saved 480-second forecasts
and verified Kalshi official settlements, and selects only the **currently
promoted model's hash** by default:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.bootstrap.forecast_policy_audit_480
```

This runs in **read-only mode**. It does not access trading credentials,
place orders, retrain any model, or modify existing forecast records.

Read these fields in the result:
- `matched_settled_markets`: exact same checkpoint samples for both gates.
- `current_strength_policy`: legacy 63%-strength policy.
- `requested_directional_policy`: proposed >63%-predicted-side probability.
- `changes.fewer_skips`, `extra_correct`, `extra_incorrect`: transparent
  coverage/accuracy tradeoff from qualifying additional market decisions.
- `accuracy_change_percentage_points`: *conditional qualified* accuracy
  difference (can be **negative** while qualified correct count rises).
- `unchanged_model_brier_score`: identical for the two policies because
  **both consume exactly the same probabilities**.
- `excluded`: no unsupported reinterpretation of unhealthy, late,
  unresolved, legacy 240s, or different-model forecasts.

The command is **not a comparison between two retrained models and cannot
establish improved probability calibration**. Even where it reports better
historical accuracy or fewer SKIPs, the same dataset might overlap with model
training; results are descriptive, not a fresh untouched proof of performance.
To test an actual revised probability estimator, both old and changed outputs
must be generated using only information available at each checkpoint, and
then scored against untouched later settlements. Without trained or fitted
weights or properly sourced BRTI history, it is not valid to claim extra
accuracy for new indicators.

For an exploratory, mixed-model historical audit (not a fair active-model
comparison), use:

```powershell
& $py -m kalshi_edge.bootstrap.forecast_policy_audit_480 --all-models
```

Relevant tests:

```powershell
& $py -m pytest -q tests/bootstrap/test_single_model_directional_gate_ui.py tests/bootstrap/test_forecast_policy_audit_480.py tests/bootstrap/test_final_forecast.py tests/trading/test_final_only_eight_minute.py tests/bootstrap/test_manual_trade_javascript.py
```

## Do not reinterpret the historical charts

The archived 69% training screenshot is an actual *old 69%-strength evaluation*
and should remain labeled accurately. Historical 63%-strength evaluations also
do not retrospectively become evaluations of a 63%-directional gate. The
read-only paired script explicitly calculates the new gate from saved forecasts.

## Status

This change fixes a mismatch between a displayed percentage and a separate
internal policy. It **does not** implement the previously requested calibrated
incremental effect of BRTI flip/reversal, signed whale trades, or candle
features on the live probability. Those inputs require externally valid
signal evidence and a calibrated integration, not hand-inflated percentage
weights. The existing single model remains the authority.
