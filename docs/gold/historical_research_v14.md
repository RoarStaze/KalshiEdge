# GoldPredict V1.4 — Historical Data and Four Research Components

**Status:** Historical reconstruction and statistical modeling are **research-only**. Do not enable Gold AutoTrade or treat these predictions as validated settlement probabilities.

## What V1.4 actually implements

- **Pyth history backfill:** Bounded 1-minute historical GOLD OHLC GETs from Pyth Pro using `PYTH_API_KEY`. Requests are split into one-day chunks, with a maximum 30-day window per invocation. The bearer key is never written to disk. Each response is ingested into the existing SHA-256-named Pyth history evidence format.
- **Historical reconstruction:** Join a SHA-256-verified KXGOLD15M inventory with imported Pyth OHLC. Use only bars that would have ended at least one full minute before the *assumed* historical checkpoint (market close - 480 seconds). Reject gaps, contradictory outcomes, corrupt evidence and insufficient past history.
- **XGBoost:** Fit a supervised baseline to gold return, range and volatility features from historical gold bars.
- **Time-series model:** Fit a **lagged 20-minute-return logistic classifier**, not TimesFM or a transformer. Train/test features are point-in-time restricted by conservative price-bar lag assumptions.
- **Regime detection:** Fit three unsupervised KMeans regimes **only on training features**, then evaluate regime-enhanced XGBoost on later market periods.
- **Financial language model adapter:** Optional **locally installed FinBERT** scores timestamped financial headlines. News counts only when both publication and first-observation timestamps precede the decision. There is no automatic news scraper and no network model download; missing news never becomes fabricated neutral evidence.
- **Chronological evaluation:** Research train/test splits separate complete UTC days, apply a **hypothetical** 24-hour label-availability embargo, and report accuracy, Brier, log loss and 69% prediction-gate coverage. A historical outcome observed today cannot prove when its label actually became available.

## Critical restrictions and assumptions

The gold dataset gate in `src/kalshi_edge/gold/dataset_readiness.py` still reports **zero production-eligible training rows**. This is intentional. These examples combine **Kalshi-reported results** with Pyth-formatted historical OHLC, but do not yet independently establish:

1. Authenticated signed oracle settlement labels and the exact Pyth channel Kalshi used.
2. Contract-specific one-minute start/end candle boundaries, rounding or historical rule variations.
3. That retrospective Pyth OHLC values were originally published at the modeled decision timestamp (the extra one-minute bar embargo is conservative, not a substitute for publication receipts).
4. The actual release moment of a Kalshi settlement label (research assumes it is available 24 hours after market close, not an established Kalshi guarantee).
5. News revision/release authenticity for a historical finance-language backtest.

**Therefore all models remain disabled for trading, no deployable model weights are saved, and any replay metrics are descriptive only.** Synthetic test results are never gold-market performance claims.

## 1. Collect or refresh the market inventory

```powershell
cd "$HOME\Desktop\KalshiEdge"
$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.gold --inventory --max-pages-per-tier 100 --output-root data/gold
```

This uses Kalshi's historical and live market-listing endpoints and preserves raw responses. You can use your existing recent `data/gold/market_inventories/*.json` instead.

## 2. Download Pyth gold OHLC history with your own subscription

Pyth's official API requires a Pyth Pro bearer token. A Pyth trial/subscription can be accessed through the official provider; not all history is necessarily available on every subscription.

```powershell
$env:PYTH_API_KEY = Read-Host "Enter Pyth Pro API key" -MaskInput
$from = [DateTimeOffset]::Parse("2026-10-01T00:00:00Z").ToUnixTimeSeconds()
$to   = [DateTimeOffset]::Parse("2026-10-07T23:59:00Z").ToUnixTimeSeconds()
& $py -m kalshi_edge.gold --fetch-pyth-history --pyth-from-ts $from --pyth-to-ts $to --output-root data/gold
Remove-Item Env:\PYTH_API_KEY
```

Adjust the dates to cover the full historical market inventory. Run in multiple **up-to-30-day** chunks for longer ranges. It produces immutable imports under `data/gold/pyth_history_imports/`. These imports retain the existing **unverified settlement** classification even though the tool made an authenticated HTTPS request; TLS is not a signed oracle attestation.

## 3. Build and inspect the research-only dataset

```powershell
$inventory = Get-ChildItem "data/gold/market_inventories/*.json" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
$history = Get-ChildItem "data/gold/pyth_history_imports/*.json"
if (-not $inventory -or -not $history) { throw "Inventory and Pyth history imports are required" }
$arguments = @("--build-historical-research", $inventory.FullName, "--output-root", "data/gold")
foreach ($item in $history) { $arguments += @("--pyth-artifact", $item.FullName) }
& $py -m kalshi_edge.gold @arguments
```

The result reports research candidates and exclusions, plus a SHA-256 artifact under `data/gold/historical_research/`. Historical YES bid/ask snapshots retrieved after settlement are **not** used as if they had been present at the forecasting checkpoint.

## 4. Compare XGBoost, temporal and regime components

```powershell
$dataset = Get-ChildItem "data/gold/historical_research/*.json" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
& $py -m kalshi_edge.gold --compare-historical-research $dataset.FullName --min-train 100 --min-test 30
```

This emits a comparison report. If there are insufficient research candidates or no chronological label-purged split, the experiment refuses to train instead of inventing metrics.

## 5. Optional FinBERT macro language features

If you already have a **locally installed** compatible FinBERT model, supply an external JSON array of news events, each with a real `headline`, `published_ns`, and `first_observed_ns`. The two timestamps must be Unix nanoseconds and must describe genuine historical first availability. To score locally, add:

```powershell
$arguments += @("--macro-events", "data/gold/news_asof.json")
$arguments += @("--finbert-local-dir", "C:\Models\finbert")
& $py -m kalshi_edge.gold @arguments
```

Install optional PyTorch/Transformers dependencies separately if using FinBERT. No FinBERT weights are bundled, downloaded by the command or assumed present. A score identifies financial tone, **not** whether gold must rise or fall.

## Evidence sources

- Pyth Pro authenticated historical history API: https://docs.pyth.network/api-reference/historical-prices/history
- Pyth Pro REST authentication: https://docs.pyth.network/pro/api/rest
- Federal Reserve vintage semantics: https://fred.stlouisfed.org/docs/api/fred/realtime_period.html

**Next production-readiness gate:** Independently map each historical Kalshi gold contract's reference observations to the correct Pyth feed and timestamps, then validate independently reconstructed outcomes before fitting a trade-eligible model.
