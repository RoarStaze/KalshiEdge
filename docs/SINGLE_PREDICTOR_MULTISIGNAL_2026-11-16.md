# Single-predictor causal multi-signal feature upgrade (2026-11-16)

## Scope

This change **does not create a second model, enable the separate multi-model
fusion artifact, lower confidence thresholds, switch AutoTrade on, or change
the probability of an already-promoted model**. Instead it expands the
*existing single classifier's feature space* using combinations of point-in-time
data already stored in the verified historical Parquet and available in the
live 1-second Binance feed.

A new model must learn whether any of these interactions improve the 480-second
outcome. The existing Train Now full-model path already requires calibration,
chronological model comparison and promotion safeguards; never skip them.

## Why historical data works without waiting for 480 new fusion records

Previously archived `derived/features.parquet` contains the raw Binance
features needed for this change, such as 15/60-second returns, 60-second realized
volatility, 15/60-second taker imbalance and volume, and normalized distance to
the strike. `training._table_to_rows` now deterministically derives interaction
features from these already-hash-verified **pre-checkpoint** columns. The original
Parquet and its manifest are not rewritten or retrospectively supplied with
unobserved BRTI.

The exact same pure computation runs in `features.build_feature_row` for
live predictions and newly created historical rows. The same transform is used
when reconstructing supervised *read views* of immutable 480s forward snapshots,
without altering the stored evidence or official settlement join.

### Derived signal features

| Feature | Formula and interpretation |
|---|---|
| `btc_signed_taker_volume_60s` | 60s executed volume × signed buy/sell imbalance |
| `btc_flow_imbalance_change_15v60` | Recent 15s buy/sell imbalance minus the 60s baseline |
| `btc_volume_burst_15v60` | 15s volume ×4 / 60s volume, clipped to [0,8] |
| `btc_near_strike_signed_flow_60s` | Signed 60s taker flow divided by 1 + absolute normalized distance to strike |
| `btc_near_strike_reversal_15s` | Signed 15s return against the current strike-side position, normalized by 60s volatility and clipped to [-8,8] |
| `btc_interaction_available` | 1 only when the required *causally available* price, volatility and flow inputs are coherent; otherwise all derived numeric values and the flag are zero |

**These are BTC/Binance signal interactions, not the independent CF Benchmarks
BRTI reversal probability and not verified individual whale trades.** The existing
BRTI near-strike veto retains priority as a separate explicit SKIP guard. It is
not legitimate to backfill BRTI by assuming Binance candles equal the benchmark.
Kalshi confirms the 15-minute contract compares minute-averaged CF Benchmarks
prices: https://help.kalshi.com/en/articles/13823838-crypto-markets .
Binance's documented 1-second kline includes OHLC, executed volume and taker-buy
base volume: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md .

## Causality, reproducibility and prior models

- The source feature columns were already created at the checkpoint. The
  transform reads no labels, future candles, settlement revisions, model
  predictions, or private credentials. It has no network dependencies.
- Missing source columns cannot be invented. If all required columns exist but
  one required signal is invalid or unavailable, use all-zero *derived features
  plus an explicit zero availability flag*; do not assert a bullish or bearish
  signal.
- Every derived value has deterministic finite caps, is idempotently computable
  from the verified raw row, and is prefixed `btc_` so the existing BTC-family
  ablation cannot accidentally retain these interactions when disabling BTC.
- The feature engineering policy identifier is included in the model's
  `training_config_hash`. The serialized promoted model's required feature
  list specifies whether it actually requires the derived inputs.
- Old promoted model bundles ignore these new columns. The current
  `live.py` scoring and AutoTrade decision/gate behavior are unchanged.
- The model's existing feature-family ablation may reject BTC signals if they
  do not improve the relevant development evidence. No positive effect from
  these engineered features is claimed or forced.
- The prior model can continue on its **existing** feature width; adding new
  feature dimensions to a fitted classifier requires a *new trained and
  calibrated model*, not a dishonest warm-start that quietly changes input
  shape. Reuse the verified 31k-market dataset rather than requiring 480
  new forward examples for these **Binance-derived** features.

## Local update and verification

Fully disarm AutoTrade and stop the live predictor before changing the
installed code. Preserve uncommitted work before `git pull`.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Confirm code and feature parity.
& $py -m pytest -q tests/bootstrap/test_causal_signal_interactions.py tests/bootstrap/test_features.py tests/bootstrap/test_live.py tests/bootstrap/test_forward_feature_evidence_480.py

# Inspect the active promoted model; old model ignores the new signals.
@'
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.live import load_default_model
from kalshi_edge.bootstrap.features import CAUSAL_INTERACTION_FEATURE_NAMES, CAUSAL_INTERACTION_POLICY
bundle, pipeline = load_default_model(BootstrapSettings().bootstrap_dir)
used = sorted(set(pipeline.outcome_feature_names) & set(CAUSAL_INTERACTION_FEATURE_NAMES))
print("active_model_hash:", bundle.bundle_sha256)
print("signal_policy_available:", CAUSAL_INTERACTION_POLICY)
print("current_model_uses_derived_interactions:", bool(used))
print("active_derived_features:", used)
print("trained_btc_feature_count:", sum(x.startswith("btc_") for x in pipeline.outcome_feature_names))
'@ | & $py -
```

To potentially **improve forecast probability**, run the existing **Train Now**
full-model workflow on the Train page when its verified historical dataset
and independent test partitions are ready. The new classifier training
features are derived from already-saved Parquet columns in memory, so no
480-new-market waiting period is required *for these BTC interactions*. A
trained model will influence production only after the existing validation
and promotion policy accepts it; restart to load that promoted bundle.

**Do not run `signal_fusion_480 fit` for this goal:** that is the separate
optional multi-model layer you explicitly postponed.

## Remaining evidence gap

GitHub contains code and unit fixtures, not the user's local 31k-market
feature rows or promoted model coefficients. There is no verified performance
result for these interactions until the user executes the existing chronological
train, calibration and untouched lockbox comparison. Additional independent
BRTI flip-indicator integration into the classifier still needs historical
BRTI-at-checkpoint evidence; signed Binance flow is not a substitute for
BRTI-referenced settlements. Do not inflate probabilities, loosen SKIP
safeguards, or claim reduced SKIPs without paired evidence.
