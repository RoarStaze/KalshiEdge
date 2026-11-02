# Historical 480-second AI confidence replay from recorded 1Hz predictions

**Purpose:** Diagnose frequent SKIPs after PR #69 moved the final forecast to eight minutes. Older four-minute final forecasts and their original official settlement outcomes are useful labels, but those *four-minute predictions* cannot be used as eight-minute predictions. The new tool recovers **actual previously logged one-second model outputs at the 480-second checkpoint**, without backfilling forecasts or prices.

## Run without restarting KalshiEdge

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.shadow_eight_minute
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.checkpoint_confidence
```

The first command reads only local files:
`data/bootstrap/final_forecasts`, official stored settlements,
and `data/bootstrap/predictions/YYYY-MM-DD/predictions.jsonl`.
It uses at most the newest 100 eligible settled **legacy 240s** markets
by default; `--last-markets 200` expands the bounded window.

The second command reports *genuine immutable* 480s forecasts from the new
strategy separately from legacy 240s immutable records.

## Evidence integrity

- Only markets having an officially verified, local settled label and
  a legacy 240-second final record are eligible for the retrospective replay.
- For each old market, select the **earliest** archived observation whose
  original prediction timestamp was in the original 480s checkpoint window
  `close-480s <= prediction_ts <= close-478s`.
- If the earliest 480s tick is unhealthy, incomplete, or invalid, **never
  seek a later healthy tick** to artificially improve hit rate.
- Repeated records at the exact earliest timestamp make that market ambiguous
  and are excluded. Repeated ticker labels are excluded in full.
- The observation must match the ticker, its own contemporaneously reported
  seconds remaining, market side, probability complement, and unchanged
  confidence definition.
- Recorded missing/no-healthy data is NOT a SKIP with modeled probability:
  only genuine, healthy p-values can be retrospectively scored, including
  confidence-gated SKIP counterfactuals.
- The report uses the already-implemented proper-scoring reliability
  calculator for Brier, log loss, calibration, confidence cohorts and
  same-checkpoint Kalshi midpoint comparison.
- No prediction, label, model, final record or journal is modified.
  No Kalshi credentials, trading API calls or orders are involved.

## Interpretation

This produces **retrospective shadow evidence**, not new immutable final
records, actual AutoTrade opportunities, live profitability, causally
independent prospective validation, or proof that raising confidence would
improve outcomes. If the old one-second prediction logs are incomplete or
were not captured at eight minutes, the report explicitly leaves those
markets unscored.

If the replay's 70-85% probability bucket wins substantially MORE often than
its predicted probabilities over multiple independent future periods, then
underconfidence might warrant a carefully fit, horizon-specific calibrator.
If not, increasing probability without new information is merely misleading.
An eight-minute probability may naturally be closer to 50% because more
price movement remains before settlement.

Important: Training features already include a 480-second checkpoint in
`BootstrapSettings`; the live model still uses the same promoted ensemble
and calibration. A documented legacy 240-second promotion diagnostic means
we need a separate 480s candidate and independent lockbox comparison
before considering model replacement.

The user's historical one-trade-in-20-hours frequency is NOT explained by
model-confidence SKIPs alone: separate saved-order audit metrics are needed
for fee/edge rejections, quote availability and actual IOC fills.

## Supporting sources
- Strictly proper scoring: Gneiting & Raftery (2007).
  https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
- Model calibration and the danger of fitting on train predictions:
  https://scikit-learn.org/stable/modules/calibration.html
