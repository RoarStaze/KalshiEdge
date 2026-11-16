# No-training matched-coverage signal error audit — 480s Bitcoin contracts

## Verified starting point

Previously saved, same-model historical comparisons on **120 markets** found:

| Forecast calculation | Brier | Log loss | All-market accuracy |
|---|---:|---:|---:|
| Current promoted model | **0.14808** | **0.45449** | **77.50%** |
| Fixed 75% model / 25% signals combination | 0.15104 | 0.46786 | 77.50% |
| Fixed signals-only heuristic | 0.18620 | 0.56107 | 76.67% |

These are **descriptive user-reported results**, not a fresh verification in the GitHub CI environment. The historical combination changed **zero predicted directions** relative to the base model and reduced coverage at unchanged thresholds. There is no established basis for deploying it as a new calibrated probability.

Kalshi's 15-minute Bitcoin market compares **CF Benchmarks BRTI opening- and closing-minute averages**, not Binance last trade; per-market contract rules govern. See <https://help.kalshi.com/en/articles/13823838-crypto-markets>.

## What this code actually tests

`python -m kalshi_edge.bootstrap.model_signal_error_audit_480` is a NEW **read-only, outcome-independent-ranking** analysis.

It reads only previously issued and authenticated 480-second forecast/feature snapshots and saved verified official settlements from the same active trained model. It explicitly excludes invalid, late, missing or non-probability-skip records and applies the unchanged near-strike BRTI flip veto.

It compares *model decisions* — not a fabricated percentage:

1. **Original model gate**: model side probability strictly greater than a listed threshold (63, 68, 70, 73, 75, 78, 80 or 82 percent), plus existing BRTI veto.
2. **Model + signal-conflict selection**: at each threshold, retain exactly **K** markets, where K is the original model's qualified count. Use fewest contradictory signal families first, breaking ties by highest original model predicted-side probability. The four fixed signal families are:
   - Direction of actual contemporaneous BRTI relative to opening strike (signed, volatility-normalized distance)
   - **Consensus** of two causal 15s/60s closed-candle momentum signs (counted as one family)
   - 60s Binance aggressive-buy versus aggressive-sell imbalance
   - Kalshi yes-taker versus no-taker imbalance
3. **Previously tested fixed blend, used only as a selection ranker**: choose the top K by the earlier uncalibrated 75/25 model-plus-signals *side confidence*, but keep **the original model's side and original model's reported probability** for scoring. This tests whether its reordering adds information *without* deploying a numeric probability blend.

No family weights, fitted calibration coefficients, new estimator, label-dependent ranking, user credentials, real orders or network lookups are involved. An individual whale-trade detector is not available.

All three decisions have the **same K and therefore the same coverage**, so their differing conditional accuracy — if any — indicates a different market selection rather than a coverage artifact. The command also reports how many forecasts each candidate would need to select **below the original probability gate**: those proposed rescues are *not production compatible* without a separate policy change and validation.

### Metrics and cautions

For each threshold and ranker:
- Qualified, skipped, correct, incorrect, qualified accuracy, identical coverage
- Number of **replacements/swaps** compared with the original model's actual qualified set
- Number of replacement decisions whose model-side probability was **at or below the gate**
- Difference in correct forecasts and qualified accuracy versus the original policy

Also report *descriptive* flagged and unflagged model error rates for each signal family. A signal correlating with mistakes in these markets is not proof it detects out-of-sample errors.

The 120 markets were already inspected in several historical sweeps. Selecting the best-looking policy from these results is **data snooping**. See [White, A Reality Check for Data Snooping](https://doi.org/10.1111/1468-0262.00152). A real probability adjustment needs independent calibration/validation; see [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html).

**Nothing in this patch changes live model weights, probability output, SKIP threshold, coin-flip veto, AutoTrade, or saved forecasts.** You cannot honestly guarantee an immediate positive live accuracy AND coverage change from this fixed historical cohort without fitting new weights, an independently validated causal structural formula, or new prospective settlements.

## Windows PowerShell

Before any `git pull`, preserve uncommitted changes, such as tracked `src/kalshi_edge.egg-info/*`, backup files or project `tools/` folders. Do **not** blindly reset or clean them.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"

# Read-only current-model + signal conflict ranking audit, eight gates
& $py -m kalshi_edge.bootstrap.model_signal_error_audit_480

# Detailed numerical evidence and mistakes per signal family:
& $py -m kalshi_edge.bootstrap.model_signal_error_audit_480 --json

# Deterministic no-fit test fixtures (no orders, network or model changes)
& $py -m pytest -q tests/bootstrap/test_model_signal_error_audit_480.py
```

For the earlier known promoted-model identity if it has been replaced:

```powershell
& $py -m kalshi_edge.bootstrap.model_signal_error_audit_480 --model-hash 5c249cd522d23b77db85670b21adc3b516e052278510b2f556b4764679c22657
```

No historical actual performance result can be reported from GitHub alone because the user's immutable feature snapshots and official settlements are local to their computer. The script produces actual results immediately from those local records, subject to their availability.

## What counts as genuine progress

A promising research pattern is **fewer mistakes at exactly matched K with no lookahead**, ideally while avoiding any attempted rescue at or below the current live gate. But this is descriptive discovery.

To establish an actual improvement without training:
1. Freeze ONE clearly specified signal-conditioned decision rule (and the model hash) before new outcomes settle.
2. Run the frozen alternative purely read-only on consecutive **new** 480s forecasts using only signals captured then; preserve all model decisions and BRTI safety checks.
3. Compare paired accuracy, coverage and, if intended for money, executable EV/fills/fees on the new settlements.
4. Change live policy only if the prospective data supports the improvement and the change is authorized.

The previous fitted fusion `signal_fusion_480` remains separate and is **not activated** by this analysis. Do not use its 480-minimum training command for this task.
