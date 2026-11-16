# Single-path live multi-signal fusion at the 480-second checkpoint

## Deployment contract

This implementation adds **one optionally fitted probability layer to the existing live prediction**; it does not generate an alternate prediction, an AutoTrade order, or a shadow/challenger path. If no validated local fusion artifact exists for the **exact active promoted model hash**, the existing calibrated forecast and existing 63% model-strength rule remain unchanged.

**The code can be merged without implying the fusion coefficients are already trained or deployed on a user's laptop.** GitHub does not have the user's active hash-sealed local model bundle or immutable forward signal/settlement data. Do not claim improved accuracy, increased coverage, or new numeric signal contributions until local fit reports `DEPLOYED_RESTART_PREDICTOR` and a new official settlement evaluation independently supports them.

### Numerical inputs

The direct final probability uses a trained, regularized logistic stack with chronological calibration:

| Fitted term | Meaning and limits |
|---|---|
| `base_logit` | Original fully calibrated structural + ML + Kalshi market-prior + residual stack |
| `brti_distance_bps` | Signed actual BRTI-to-opening-target distance, not Binance spot |
| `brti_basis_bps` | Simultaneous BRTI minus Binance reference |
| `brti_signed_risk_z` | Distance sign multiplied by causal BRTI trailing volatility-normalized strike distance |
| `near_strike_reversal_bps` | Signed short-window price momentum against the BRTI strike-side, downweighted away from the strike |
| `btc_return_15s_bps`, `btc_return_60s_bps` | Closed-candle momentum indicators derived from the already-running Binance 1-second market-data feed |
| `btc_taker_buy_imbalance_60s` | Aggressor buy/sell imbalance; not a claim about whale identity |
| `signed_aggressor_volume_btc` | Signed aggregate executed 60-second volume, NOT a count of distinct large purchases |
| `kalshi_yes_taker_imbalance_60s` | Kalshi contract-direction flow |

No new TradingView subscription or undocumented indicator API. A future separate large-print microstructure model would require recorded individual public trades and matching point-in-time history.

The feature vector is strictly validated and excludes absent/invalid indicators, future timestamps, stale BRTI or Binance feeds. Fusion is used **only within the actual 480s checkpoint window**; it must not be extrapolated to 840s, 240s or the last minute. Outside the checkpoint, and when a required verified feature is unavailable, the original model's probability is used. Existing model-failure and near-strike SKIP protections still apply. The live final forecast uses the **strict predicted-side probability gate** `max(P(YES), P(NO)) > .63`, with the BRTI near-strike veto retained. Fusion v2 now uses exactly this same gate for holdout acceptance; the older v1 validation mistakenly used `2 * abs(P(YES) - .5) >= .63` (at least 81.5% predicted-side probability). Neither 63% nor any model-estimated probability guarantees an empirical win rate.

### Source and validation integrity

Training uses only:
- a previously issued immutable final 480s forecast, including its original model probability and contemporaneous BRTI risk z;
- a hash-checked forward feature snapshot originally written **after** that forecast, containing causal signals recorded no later than the forecast;
- a matching verified official Kalshi settlement saved after the market close.

The `forward_feature_evidence_480.py` collector's BRTI feature capture requires its independently checked same-tick BRTI and Binance feed timestamps; no post-settlement backfilling is permitted.

The local supervised fit is fixed in code (no parameter search): minimum 480 eligible markets of the **same currently active base bundle**, a chronologically earlier development block, a separate 80-case sigmoid intercept calibration block, and a final untouched 100-case holdout. Eight-market embargoes and available-by-time settlement checks apply. A fixed L2-regularized logistic model (C=.05) learns signal contributions. Original model probability stays as an explicit first input, avoiding blind voting of strongly correlated model and candle features. If the fitted coefficient on that input is non-positive, the fit is refused.

A local fusion artifact is only activated if the final paired holdout demonstrates:
- at least 0.0001 absolute improvement in Brier score and log loss;
- no reduction in 63%-strength decision coverage or number of correct forecasts under the **same** coin-flip veto;
- conditional qualified accuracy no more than 0.5 percentage points lower.

This is an engineering gate, **not a guarantee of future profits**. Signal selection and evaluation design should be reviewed on genuinely new data rather than repeatedly optimizing the same observations.

The artifact is JSON (no pickle), signed by its content SHA-256, tied to a particular underlying model bundle hash, and exclusive-created. A damaged active file blocks predictor startup; a valid artifact from an old model cannot contaminate a newly promoted model.

### Fusion v2 compatibility and safe migration

Prior `live-480-fitted-multisignal-v1` artifacts were validated at the
obsolete strength threshold, not the live final-forecast probability threshold.
**Do not silently retain them.** The loader explicitly refuses an existing
signed v1 artifact to avoid applying a model whose live 63% approval was never
established. This can stop predictor startup until the old optional artifact
is moved away; the underlying promoted model remains unchanged.

If `status` reports `legacy v1 fusion artifact`, first stop the predictor
and disarm AutoTrade. Back up the old **local file for the reported model hash**
before retrying:

```powershell
cd "$HOME\Desktop\KalshiEdge"
$py = ".\.venv\Scripts\python.exe"
$root = "data\bootstrap"
$hash = (& $py -c "from kalshi_edge.bootstrap.live import load_default_bundle; from kalshi_edge.bootstrap.config import BootstrapSettings; print(load_default_bundle(BootstrapSettings().bootstrap_dir).bundle_sha256)").Trim()
$src = Join-Path $root "models\fusion\$hash.json"
if (Test-Path $src) {
    New-Item -ItemType Directory -Force (Join-Path $root "models\fusion\legacy-backups") | Out-Null
    Move-Item $src (Join-Path $root "models\fusion\legacy-backups\$hash-v1.json")
}
& $py -m kalshi_edge.bootstrap.signal_fusion_480 status
```

Check whether **enough independently verified, same-model 480s settlements**
exist using the status command. Only rerun `fit` if the readiness report
indicates the data is present. The v2 fit keeps the existing chronological
fit/calibration/holdout sizes, unchanged 63% production gate and coin-flip
veto, and writes a new artifact only after *the current live policy*
satisfies the existing score and coverage safeguards. This does **not**
claim a measured improvement on this user's unavailable local history.

### Local commands (Windows PowerShell)

Fully **disarm AutoTrade**, stop the live predictor and allow any running training worker to exit before changing model deployment.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Safe: inspect verified point-in-time forward examples.
& $py -m kalshi_edge.bootstrap.forward_feature_evidence_480

# Safe: read active model identity, whether fusion is fitted and sample counts.
& $py -m kalshi_edge.bootstrap.signal_fusion_480 status

# Explicit fit/activation: does not submit orders or arm AutoTrade.
# If insufficient or no validated paired improvements, returns REFUSED / HOLDOUT_NOT_BEATEN.
& $py -m kalshi_edge.bootstrap.signal_fusion_480 fit

# Check status again, then restart the live predictor to load the sealed artifact.
& $py -m kalshi_edge.bootstrap.signal_fusion_480 status

# Local regression suite for this behavior.
& $py -m pytest -q tests/bootstrap/test_signal_fusion_480.py tests/bootstrap/test_live.py tests/bootstrap/test_final_forecast.py
```

**Note:** The `fit` command reports `DEPLOYED_RESTART_PREDICTOR` only after a successful numerical fit and unconsumed file write. If it reports `INSUFFICIENT_VERIFIED_DATA`, keep collecting new immutable final checkpoint snapshots and official settlements; this code cannot and must not invent that private history.

### Multimodel expectation

The original KalshiEdge pipeline is *already a learned multimodel stack*. A new independent BRTI/reversal/flow layer may improve the marginal prediction only when it carries evidence beyond the original stack and its Kalshi market-prior. The expected outcome is **uncertain** without the user's matched time-ordered dataset. Wider information sets can improve calibration or forecast coverage but also increase overfitting; no credible point estimate is available here.

Sources:
- [Kalshi, Crypto Markets (2026)](https://help.kalshi.com/en/articles/13823838-crypto-markets) — opening- versus closing-minute CF Benchmarks index average.
- [Cont, Kukanov, Stoikov, The Price Impact of Order Book Events](https://doi.org/10.48550/arXiv.1011.6402) — order-flow imbalance evidence in equities, **not** a validation study of BTC 480-second direction.
- [TradingView support — data/indicator API](https://www.tradingview.com/support/solutions/43000474413-i-need-access-to-your-api-in-order-to-get-data-or-indicator-values/) — no public indicator retrieval API.
- [scikit-learn: Probability calibration](https://scikit-learn.org/stable/modules/calibration.html) — independent calibration block and small-sample overfitting cautions.
