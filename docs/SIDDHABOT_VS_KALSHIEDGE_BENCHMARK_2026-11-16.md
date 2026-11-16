# SiddhaBot vs KalshiEdge — genuine prospective 480-second comparison

**Isolation:** SiddhaBot's [original code](https://github.com/SiddhaBasu/kalshi-bot)
runs from a separate, gitignored checkout and Python virtual environment,
at pinned revision `00f6c52b74aa17dcb69971b4c9480db7d173bfcc`.
No third-party source or trained artifact is redistributed in KalshiEdge.
Its own feature-generation code, 23 feature columns, pretrained XGBoost model,
and calibration routines generate its predictions. The live KalshiEdge
predictor, current model, 63% forecast gate and AutoTrade remain unchanged.

**This is not a retrospective backtest.** SiddhaBot's historical features
depend on Coinbase candles, Kalshi summary quotes, quote-age observations and
trailing settlement bias. Those are not interchangeable with KalshiEdge's
separate Binance/Kalshi parquet features. Reusing an unrelated trained artifact
with fabricated feature substitutions would make the comparison meaningless.

## Setup on Windows (PowerShell)

From your KalshiEdge repository with `.venv` already created:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
.\scripts\siddhabot_benchmark.ps1 -Mode Install
```

The installer clones the pinned upstream into
`data/bootstrap/benchmarks/siddhabot/repo` and installs upstream's original
`requirements.txt` into `data/bootstrap/benchmarks/siddhabot/venv`.
It does not install or modify KalshiEdge's own Python dependencies.
An active internet connection is required. The checkout is kept out of GitHub.

Launch these **in separate terminals**, alongside your existing
`kalshiedge start` terminal:

**Terminal 1 — SiddhaBot source feeds, no trading:**

```powershell
cd "$HOME\Desktop\KalshiEdge"
.\scripts\siddhabot_benchmark.ps1 -Mode Serve
```

It runs the upstream data/feature service at `127.0.0.1:8001`. The launcher
forces `SIMULATION_MODE=true`, `BTC_LIVE_TRADING_ENABLED=false`,
`BTC_PAPER_TRADING_ENABLED=false`, and disables the unrelated weather
and whale-scanner operations. Do not change these overrides. Upstream
may need time to establish its candle, quote, and settlement-bias history.

**Terminal 2 — SiddhaBot read-only inference capture:**

```powershell
cd "$HOME\Desktop\KalshiEdge"
.\scripts\siddhabot_benchmark.ps1 -Mode Capture
```

This invokes SiddhaBot's original `_build_live_features()`,
`_load_model()`, `FEATURE_COLUMNS`, and original Platt/live calibration
functions, then persists a **single immutable** probability per market.
Neither process submits an order. Capture requires both the start and finish
of upstream feature construction to lie within **five seconds** of the exact
480-second pre-close checkpoint. Late or incomplete inputs result in no
saved prediction. The first valid capture starts the benchmark period.

**Terminal 3 — side-by-side report:**

```powershell
cd "$HOME\Desktop\KalshiEdge"
.\scripts\siddhabot_benchmark.ps1 -Mode Report
```

Machine-readable alternative:

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.siddhabot_comparison --json
```

The report reads KalshiEdge's existing immutable final-forecast journal and
officially verified Kalshi settlement cache. Keep `kalshiedge start` running
so markets continue to be recorded and settled.

## Metrics and fairness

The report uses **the same population of officially settled KalshiEdge 480s
markets whose checkpoint was after the first valid SiddhaBot capture**.
It never compares incompatible built-in backtests or unrelated time periods.

| Metric | KalshiEdge | SiddhaBot |
|---|---|---|
| Decision | `max(p_yes,1-p_yes)>0.63` | same threshold |
| Correct / incorrect | compares predicted side to official YES/NO | same |
| Accuracy | correct / qualified | same |
| Coverage | qualified / total settled period markets | same |
| Skip | nonqualifying or unavailable probability | same |
| Brier | scored on exact paired numeric-probability subset | same |

KalshiEdge's **actual locked-policy** accuracy/coverage is also shown
separately. That real policy includes health/coin-flip vetoes, unlike the
symmetric probability-only 63% research comparison. No benchmark calculation
modifies an actual KalshiEdge forecast or causes AutoTrade activity.

A missing SiddhaBot capture counts as a skip on the common denominator.
Captured probabilities outside the checkpoint tolerance, mismatched tickers,
invalid confidence or incompatible artifacts are rejected. Settlements that
are unresolved or fail the existing official-integrity audit are not scored.

**Limits:** The denominator includes only markets that KalshiEdge saved
as 480s final records. Both engines need to be running to make this useful.
Differences of up to five seconds in checkpoint timing and cross-venue data
can still affect comparability; no claim of exact simultaneous observation is
made. The benchmark reports statistical observations, not profit after
fees/market impact. If no contemporaneous officially settled markets have
both probabilities, Brier and SiddhaBot accuracy are `N/A`, not fabricated
numbers. Fresh prospective samples are required to establish superiority.

## Regression test

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/bootstrap/test_siddhabot_comparison.py
```

## Upstream risk and provenance

- Exact upstream Git commit is pinned and logged per checkpoint.
- Each output records the XGBoost artifact SHA-256 and separate inference
  start/finish timestamps; changed artifacts are detected in the report.
- The clone includes third-party code and its pinned dependencies. Review them
  before running; they are outside the KalshiEdge project's verified code.
- The user's private Kalshi credentials are never copied to the benchmark.
- No historical SiddhaBot output is manufactured from KalshiEdge features.
