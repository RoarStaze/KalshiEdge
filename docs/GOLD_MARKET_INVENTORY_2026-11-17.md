# KXGOLD15M Stage 2a — public market inventory

This is a read-only first part of Stage 2 of the 2026-11-16 gold predictor plan. It **does not** produce settlement labels, Pyth price observations, training examples, real-time features, forecasts, subscriptions, or orders. BTC files/configuration stay untouched.

## Windows command (after PR is merged)

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --inventory --output-root data/gold --page-size 1000 --max-pages-per-tier 5
& .\.venv\Scripts\python.exe -m pytest -q tests/gold
```

* Each run first validates the exact gold series and Pyth source identity and reads the historical market cutoff.
* GET `/historical/markets` and GET `/markets` are **both** queried with `series_ticker=KXGOLD15M`; each is independently paginated using the API's returned cursor. Default cap: 5 pages per tier, 1000 market records per page (API maximum).
* The collector preserves every page payload, query parameters, and local receipt timestamps in a newly created SHA-256-named file under `data/gold/market_inventories/`. It never overwrites a previous run.
* The summary reports unique gold tickers, cross-tier overlap, duplicates, contradictions between reported YES/NO outcomes, per-tier page counts, and which page limits were reached. **Observed results are not settlement-oracle-verified labels.**
* `pagination_exhausted=true` means the API returned an empty cursor for each tier in this scan. It **does not** prove a transactionally consistent historical snapshot. `complete_for_request_window=false`, `ready_for_training=false`, and `ready_for_live_trading=false` intentionally remain false.
* A repeating cursor, missing markets array, unexpected ticker, invalid source identity, or missing market cutoff raises an error without writing an inventory artifact. No credentials are used and no POST requests are made.
* Leave any local `.venv-timesfm3/`, `tools/`, backups or modified `egg-info` files alone.

**Source:** Kalshi official [Historical Data](https://docs.kalshi.com/getting_started/historical_data), [Get Markets](https://docs.kalshi.com/api-reference/market/get-markets) and [Get Historical Markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets).

## Unresolved blockers before model training

1. The five inspected KXGOLD15M contracts on November 17–18, 2026 had mixed EST and EDT abbreviations; literal offsets imply 75 minutes rather than 15 minutes. Request authoritative clarification / confirm with exact Pyth source candles and official settlements, without inventing an interpretation.
2. Resolve Pyth one-minute candle publication timestamps, two-decimal rounding method and fallback-to-most-recent-published rules.
3. Build point-in-time Kalshi quote/trade/candle capture and provenance, then validate only eligible confirmed market outcomes; public market list pages do not reconstruct historic quotes at the eight-minute checkpoint.
4. Define chronological train/calibration/lockbox splits using unique contracts and market-day sessions.

**Hardware:** local Windows host reports 15.7 GiB RAM and 6,141 MiB RTX 4050 VRAM. Avoid loading raw JSON and training arrays simultaneously if inventory grows beyond the default bounded scan.
