# Frozen 480s confidence-rescue research (read-only)

## What it tests

A fixed hybrid *counterfactual* strategy:

1. Keep any original AI ABOVE or BELOW final forecast unchanged.
2. If and **only if** the original final is `SKIP` for
   `CONFIDENCE_BELOW_69_PERCENT`, consider the *existing* frozen 480s
   specialist's **already logged, hash-validated** same-market observation.
3. Use the specialist side only if `status=OK` and its original
   `gate_qualified=true` at unchanged 69% confidence.
4. Otherwise SKIP. No retroactive predictions, fallback inputs, interpolation,
   overriding original predictions or reducing the quality threshold.

This research script **does not place orders, alter live forecasts, update
the weights, or operate on the live trade path**.

## Run

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.rescue_policy_480 | ConvertFrom-Json

"COUNTERFACTUAL REPLAY — ALL VERIFIED AFTER-FREEZE SETTLEMENTS"
$r | Select-Object status, settled_market_count, pending_settlements, missing_shadow_records, net_new_qualifying_forecasts, net_new_correct_forecasts, coverage_improvement, qualified_accuracy_change | Format-List

"ORIGINAL MODEL"
$r.incumbent | Select-Object qualified, correct, skipped, coverage, qualified_accuracy | Format-List

"FROZEN LOW-CONFIDENCE RESCUE"
$r.rescue_hybrid | Select-Object qualified, correct, skipped, coverage, qualified_accuracy | Format-List

"RESCUE WINS AND LOSSES"
$r.rescues | Format-List

"PRE-REGISTERED FORWARD VALIDATION (DO NOT CHERRY-PICK EARLY WIN)"
$r.forward_validation | Select-Object policy_registered_at_ns, settled_market_count | Format-List
$r.forward_validation.incumbent | Select-Object qualified, correct, skipped, coverage, qualified_accuracy | Format-List
$r.forward_validation.rescue_hybrid | Select-Object qualified, correct, skipped, coverage, qualified_accuracy | Format-List
$r.forward_validation.rescues | Format-List
```

## Accuracy-first interpretation

- **All-after-freeze replay** can show whether the hypothetical hybrid adds
  forecasts and wins in the *already viewed* markets; it is exploratory
  evidence because the proposed rule was chosen after early wins.
- **Pre-registered forward validation** begins strictly after
  **2026-11-02 13:00:00 UTC** (commit establishing the decision rule was
  2026-11-02 12:59:19 UTC). Historical wins before this cutoff cannot enter
  the forward sample. The same unchanged 69% gate applies throughout.
- **More qualifying forecasts is not automatically better accuracy**. The
  report includes incorrect rescues and the hybrid's conditional hit rate,
  and does not automatically promote the model even after 80 settled markets.
- **No settlement files?** The existing history/settlement sync needs to save
  official verified results. The scorer does not fetch or guess them.
- **No specialist file?** The hybrid keeps the original SKIP and records
  missing instrumentation. A previously missing market must never be filled
  by hindsight.

**Research objective:** both greater correct qualifying predictions *per
settled market* and credible noninferior qualifying hit rate across an
independent, multi-day dataset. Even a positive win rate does not establish
fee-aware positive expectancy without real executable asks, fees, liquidity
and order-fill evidence.
