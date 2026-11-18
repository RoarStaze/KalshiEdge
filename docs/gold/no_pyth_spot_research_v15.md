# GoldPredict V1.5 — No-Pyth historical research

This path does **not** call Pyth Pro, require a Pyth API key, request Kalshi data or modify Kalshi AutoTrade. It permits an **independent spot-XAU/USD research experiment** from a *user-supplied and appropriately licensed* 1-minute CSV.

## Data input

Expected UTC minute-start CSV format (documented for third-party `dukascopy-node` exports):

```csv
timestamp,open,high,low,close,volume
1791763200000,4000.12,4000.34,3999.97,4000.20,25
```

- **timestamp**: integer Unix **milliseconds**, UTC, marking minute START (not seconds or microseconds).
- **OHLC**: positive USD/oz prices; high >= open/close and low <= open/close.
- **volume**: optional and ignored in current features because source tick volume is not centralized spot volume.
- Timestamps must be unique and strictly increasing. Actual source minute gaps (weekends, outages) are never interpolated; affected 15-minute examples are skipped. Up to 350 MiB or 3.5 million bars per import.

**Possible sources, subject to their applicable licenses:**

1. [Dukascopy Historical Data Export](https://www.dukascopy.com/swiss/english/marketwatch/historical/) offers XAUUSD 1-minute historical OHLC for manual export. Confirm rights before using any automated downloader or training pipeline; Dukascopy's website terms restrict automated collection without prior permission.
2. [Hugging Face `karam1533/gold_1min_datset`](https://huggingface.co/datasets/karam1533/gold_1min_datset) is marked MIT by its uploader and has minute bars through September 2026 according to its dataset page. **Uploader license is not independent proof of underlying broker data rights.** Inspect original source and timestamp units before importing.
3. Other legitimately sourced XAUUSD 1-minute CSV files with the same column structure can be loaded offline. There is no scraping, token handling, website access or bundled data in this module.

## How gold prices become 15-minute examples

Research examples use hypothetical UTC 15-minute windows:

- **Window start price:** close of the one-minute bar immediately before start.
- **Window end price:** close of the last fully elapsed minute of the 15-minute window.
- **Target:** `UP` if end >= start; `DOWN` otherwise. This is **ONLY a spot-price proxy outcome** — not a Kalshi-reported YES/NO result and not Pyth's official settlement value.
- **Decision:** 7 minutes after window start (480 seconds remain). The most recent feature bar ends at minute 6, one full minute before decision, to avoid OHLC lookahead.
- **Features:** 1/3/5/10/20-minute returns, 5/20-minute volatility, ranges, moving averages, channel position and lagged 1-minute returns.
- **Training split:** UTC chronological split, with a conservative **assumed** 24-hour label visibility delay; no test rows are used in training or train-only regime estimation.

All artifacts are immutable, SHA-256-addressed under `data/gold/spot_proxy_research`. Their metadata explicitly records `eligible_training_rows=0`, `settlement_oracle_verified=false`, and `model_deployment_allowed=false`. Counts refer to **research candidates**, never production training-eligible Kalshi settlement rows.

## Commands (Windows PowerShell)

1. Obtain a legally licensed UTC 1-minute XAUUSD CSV and place it somewhere local (for example `data/gold/external/xauusd-m1.csv`). No secret or API key is necessary.

2. Update the project and build proxy research rows:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.gold --build-spot-research "data\gold\external\xauusd-m1.csv" --output-root data/gold
```

3. Compare the research models (XGBoost, lagged-return time-series model, KMeans-regime XGBoost):

```powershell
$dataset = Get-ChildItem "data\gold\spot_proxy_research\*.json" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $dataset) { throw "Import XAUUSD CSV first" }
& $py -m kalshi_edge.gold --compare-spot-research $dataset.FullName --min-train 100 --min-test 30
```

The command reports chronological accuracy, Brier score, log loss and coverage on **independent XAU/USD proxy labels**, not Kalshi trading profits. No models are saved or activated, and the existing Pyth-based historical research route is left intact.

### Optional fourth component: local FinBERT macro language features

The existing V1.4 macro adapter also works with this independent spot dataset. Supply an external JSON list of headlines with **verified `published_ns` and `first_observed_ns`** and an existing sentiment `score` between -1 and +1. Only news actually known at or before each historical decision time counts. To score the headlines with FinBERT instead of supplying scores, specify a local model directory already containing compatible weights; the tool does not download a model.

```powershell
& $py -m kalshi_edge.gold --build-spot-research "data\gold\external\xauusd-m1.csv" --output-root data/gold --macro-events "data\gold\external\news_asof.json" --finbert-local-dir "C:\Models\finbert"
```

This optional fourth component requires suitable independently timestamped historical news and locally installed Torch/Transformers packages. No economic headlines are manufactured or retrieved from future archives without point-in-time evidence.

## What this does *not* solve

- Exact Pyth `Metal.Index.1OZGOLD/USD` historical settlement values or the matching Kalshi contract time/rounding rules.
- Broader trading hours of a 24/7 oracle, where XAUUSD broker sessions may be shut.
- Correlation of broker XAUUSD price level to Pyth/Kalshi strike, bid/ask fills, market fees or scalping execution.
- Licensing rights to train on Kalshi data. Kalshi's [Data Terms of Use](https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf) expressly restrict using its data for machine-learning development, while its [Developer Agreement](https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf) describes a narrower API use policy for members' own trading. Check permission directly before using exchange data for AI training.

**Next validation gate:** Test the independent spot model on fresh gold prices with a frozen configuration. Only with separately licensed Kalshi price/outcome data and verified settlement alignment should the next stage measure whether it improves the actual contract forecasts or trade P/L. Never map spot proxy accuracy directly into Kalshi accuracy.
