# KalshiEdge: current model vs signals-only vs combined — eight gates

## Actual scope

The historical experiment now compares **THREE probability calculations**
on identical saved market snapshots: the current model, signals alone, and the
model-plus-signals blend. This is *not* three separately trained models and
not another gate-only comparison.

- **A: Current deployed single-model probability**. Uses the immutable
  `p_yes` that KalshiEdge originally recorded at the 480-second final forecast.
  If a later fusion is ever deployed, we read its frozen `unfused_p_yes` as
  recorded at prediction time instead.
- **B: Signals only**. Uses the exact same BRTI, candle/reversal and signed
  buy/sell flow signal proxies but **zero influence from the model's
  directional probability**. The 15:6:4 raw component weights are normalized
  to 60% BRTI reference, 24% candle/reversal and 16% signed flow. These
  ratios are fixed uncalibrated hypotheses, *not fitted coefficients*.
- **C: Current model + no-training multi-signal experiment**. Retains the
  existing 75/15/6/4 blend and the exact same calculation as the prior
  two-way audit: `P_combined = 0.75 * P_model + 0.25 * P_signals_only`.
  Both research calculations remain separate from live trading and cannot
  trigger orders or overwrite predictions.
- **Same qualifying markets**: only cases with immutable hash-checked
  480-second checkpoint feature snapshots, an official verified Kalshi
  settlement and complete, nonfuture BRTI/candle/flow observations.
  Uses the common `signal_fusion_480._verified_examples` evidence loader.
  Both predictors are evaluated on **exactly the same filtered rows**.
- **Same BRTI flip veto**: both skip whenever the recorded causal BRTI
  strike-distance risk-z is at or below the existing `COIN_FLIP_MAX_RISK_Z`.
- **Same eight gates**: 63%, 68%, 70%, 73%, 75%, 78%, 80%, 82%.
  A forecast qualifies only when the **predicted side probability is
  strictly greater** than the threshold. A candidate may alter direction;
  accuracy always uses its own direction against the same official result.

The existing historical gate-only study had 119 matched settled markets, while
the separate signal availability status previously reported 115 eligible
same-model snapshots. **Do not assume the new paired count is 119 or 115.**
The command prints the actual matched count and exclusions; otherwise sample
selection would silently bias the comparison.

## Exact no-training experimental adjustment

Frozen before outcome scoring:

```text
P_signals_only =
    (0.15 * P_BRTI_signed_volatility_distance
   + 0.06 * P_candle_momentum_and_reversal_proxy
   + 0.04 * P_BTC_and_Kalshi_signed_trade_flow_proxy) / 0.25

P_combined =
    0.75 * P_current_model + 0.25 * P_signals_only
```

- The BRTI component uses the immutable *checkpoint-time* BRTI-to-strike
  risk-z and BRTI/Binance basis. It becomes closer to 50/50 when strike risk
  is high (z near zero) or reference feed basis diverges.
- Candle/reversal component uses point-in-time 15s/60s BTC returns and
  signed movement against the current strike side.
- Flow component uses 60s Binance aggressive buy/sell imbalance, signed
  aggregate BTC executed volume and Kalshi yes-taker imbalance. **It does not
  identify individual whale trades or recreate TradingView indicators.**
- All inputs are validated against the exact previously saved source
  checkpoint; future bars, settlement labels, unavailable BRTI or missing
  flow history cannot be supplied retroactively.

**The numeric blend coefficients and signal-to-probability proxy formulas
are engineering hypotheses, NOT probabilities independently calibrated from
historical market outcomes.** They are fixed rather than fitted because
the user explicitly requested no retraining. They may worsen Brier score,
log loss, directional accuracy and trade coverage. The code cannot promise
that multi-signal forecasting improves performance.

## Run in Windows PowerShell

Save any local work before pulling new code, particularly tracked
`src/kalshi_edge.egg-info/*` changes and untracked backup folders; don't
blindly clean, reset or overwrite them.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Actual three-way paired comparison across all eight gates:
& $py -m kalshi_edge.bootstrap.model_multisignal_comparison_480

# Detailed machine-readable report with paired Brier/log loss/ECE/deltas:
& $py -m kalshi_edge.bootstrap.model_multisignal_comparison_480 --json

# Test pairing, hash integrity, flip veto and arithmetic; no trades/training:
& $py -m pytest -q tests/bootstrap/test_model_multisignal_comparison_480.py
```

To restrict to the previously reported trained model identity explicitly:

```powershell
& $py -m kalshi_edge.bootstrap.model_multisignal_comparison_480 --model-hash 5c249cd522d23b77db85670b21adc3b516e052278510b2f556b4764679c22657
```

The default is **the currently promoted model's hash**. No mixes of
different historic trained models are permitted.

## How to interpret the output

For every threshold **and for all three predictors**, the table reports:
`qualified`, `skipped`, `correct`, `incorrect`, `accuracy`, `coverage`,
and `incremental_accuracy` (accuracy of the additional markets admitted
when lowering the threshold from the next higher listed gate).

The JSON also reports:
- Pairwise difference tables for combined minus current model,
  combined minus signals only, and signals only minus current model, with
  correct/incorrect/qualifying counts, SKIPs and accuracy/coverage differences
- Brier score, log loss, ECE and all-market directional accuracy for each
  of the **three forecast calculations** on the exact same paired cohort
- Pairwise predicted-side disagreements between each two variants and the
  combined-vs-current mean absolute probability change
- Excluded/missing/invalid signal samples, and clear provenance warnings

**No outcome labels are passed to the no-fit probability formula.** Outcome
labels are used only to compute descriptive performance.

## Evidence limitations

This comparison is **descriptive and exploratory**. The current model may
have been developed on overlapping historic information, and the no-fit
experimental adjustment's coefficients were not validated on independent
data. Selecting the best observed threshold or changing fixed weights after
looking at this report would be data-snooping, not proof of improvement.
Even higher reported accuracy does not guarantee better realized trading
P/L after quotes, fills and Kalshi fees.

Keep the production model, current 63% directional gate, and AutoTrade
behavior unchanged. A later **pre-registered prospective** comparison on
new, untouched official settlements would be needed before considering
deployment. In particular, do **not** conflate this experimental adjustment
with the separate trained, currently inactive
`kalshi_edge.bootstrap.signal_fusion_480` layer.
