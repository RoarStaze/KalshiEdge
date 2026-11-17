# Gold Stage 2b — pre-checkpoint Kalshi market candle diagnostic

**Goal:** Check whether one-minute Kalshi market YES-price candlesticks can be retrieved from both archived and current gold markets near the candidate 480-second pre-close timestamp, without using future candle timestamps. This is a **small evidence probe**, not an approved model-training dataset or a backtest.

## Windows command

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --candle-probe "data\gold\market_inventories\1794895835446578600-a660ce89c04184eb.json" --output-root data/gold --sample-per-tier 5 --lookback-minutes 7
& .\.venv\Scripts\python.exe -m pytest -q tests/gold
```

### Selection

- Validate the existing SHA-256-named inventory, exact \`KXGOLD15M\` series and \`Metal.Index.1OZGOLD/USD\` settlement source, 480-second checkpoint and 0.69 gate before any network request.
- Select the **five most recent finalized unique tickers** in each of two tiers, excluding every ticker present in both tiers (the original inventory had 496 such duplicates), contradictory YES/NO evidence, nonfinalized states, and missing/invalid UTC close times. Depending on available eligible markets, it can select fewer than ten.
- For each market, define a candidate timestamp \`API close_time - 480 seconds\` (**not verified to be the official settlement observation**) and retrieve the preceding seven minutes of one-minute Kalshi market candles: \`start_ts = candidate - 420\`, \`end_ts = candidate\`, \`period_interval = 1\`.
- Request \`GET /historical/markets/{ticker}/candlesticks\` for archived-only samples and \`GET /series/KXGOLD15M/markets/{ticker}/candlesticks\` for current-only samples. No authenticated endpoints, subscriptions, Pyth API requests, orders or model edits.
- Reject unexpected response ticker, malformed/duplicated \`end_period_ts\`, values outside the requested window, unsupported output roots or tampered input. Save the full API JSON bodies, exact request parameters and local receipt times in a new immutable hash-named artifact under \`data/gold/candle_probes/\`.

### What this does **not** prove

- A candle with \`end_period_ts <= checkpoint\` does **not** establish the candle was published to a live user by the exact checkpoint. Kalshi documents a short possible candle lag, and historical data can be revised or published late.
- Kalshi market YES-price candles are **not** Pyth \`Metal.Index.1OZGOLD/USD\` settlement candles. The gold index data and the EST/EDT inconsistent contract text are still unresolved.
- Even when \`market.status=finalized\`, result is an official **reported** API outcome, not an independently reconstructed oracle label. The market quote at the eight-minute checkpoint also does not prove executable bid/ask depth or order fills.
- The five-per-tier selection is intentionally bounded and biased to recent markets; no calibration, backtest accuracy or prospective profitability can be inferred from it.

**Permanent fail-closed fields:** \`checkpoint_verified=false\`, \`point_in_time_publication_verified=false\`, \`settlement_oracle_verified=false\`, \`ready_for_training=false\`, \`ready_for_live_trading=false\`.

**Primary sources:** Kalshi [live market candlesticks](https://docs.kalshi.com/api-reference/market/get-market-candlesticks), [historical market candlesticks](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks), [historical partition routing](https://docs.kalshi.com/getting_started/historical_data).

**Next gate:** inspect the live API sample, verify how many historical samples contain meaningful quote data before candidate time, and design truly prospective publication-timestamp capture. Do not enable training or orders from this artifact.
