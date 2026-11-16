# Compare KalshiEdge versus SiddhaBot on previous BTC15M markets

This provides a **one-command, read-only historical comparison**; it does
not wait for newly settled markets and does **not** enable either AutoTrade.

In PowerShell, with KalshiEdge's existing research-enabled `.venv`:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
.\scripts\siddhabot_benchmark.ps1 -Mode Historical
```

Equivalent direct Python command:

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.siddhabot_historical
```

Optional larger run (last 1,000 officially settled saved finals):

```powershell
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.siddhabot_historical --max-markets 1000
```

Add `--json` for complete machine-readable output.

## What the command actually evaluates

- Loads the existing immutable **480-second** KalshiEdge final predictions.
- Checks their original officially verified Kalshi settlements.
- Reads SHA-verified historical Binance **BTCUSDT 1-second Parquet**
  archives that KalshiEdge already downloaded; no rebuild or retrain.
- Fetches the original SiddhaBot pretrained XGBoost JSON and Platt
  calibration from upstream commit `00f6c52b74aa17dcb69971b4c9480db7d173bfcc`
  on first run (or reuses the earlier installed checkout), verifying Git
  blob digests. Internet is only needed if these original model artifacts
  are not locally cached.
- Reconstructs SiddhaBot's 23 model inputs at the same 480-second
  checkpoint, with explicit limitations below, and runs frozen
  pretrained XGBoost + original Platt calibration.
- Uses **strictly greater than 63% directional probability** for BOTH
  models, evaluates on exactly the same subset of earlier settled markets,
  and prints accuracy, coverage, qualified/correct/incorrect/skips,
  plus paired Brier score. The actual KalshiEdge final policy is shown
  separately, since its risk/health gates differ from the common test gate.

## Important qualification — reconstructed, not native SiddhaBot data

**This cannot be advertised as an exact, original SiddhaBot backtest.**
KalshiEdge stores Binance BTCUSDT history, while SiddhaBot's original
trained feature distributions use Coinbase BTC-USD candles and quotes
from its own live Kalshi snapshot database. Two quote-change fields
(`quote_age_seconds` and `btc_move_since_quote`) are not stored as
historical samples in KalshiEdge; the offline replay sets them to zero
**as proxies** and exposes this approximation. Upstream's published frozen
model feature-importance report assigned those fields zero importance,
which does **not** establish that imputing zero is distribution-neutral.

The settlement-bias input is derived from **previously closed**
officially verified markets, assuming the result was available at least
60 seconds after the previous market closed. Actual historical publication
timestamps are not available to independently prove that assumption.
Coinbase's volume/spot discrepancies and asynchronous quote availability
may materially affect the result.

Use this comparison to get a **descriptive direction and coverage
comparison**, not proof that one bot's unmodified live implementation
would have outperformed the other. A truly faithful retrospective test
needs SiddhaBot's original historical Coinbase candles and Kalshi
timestamped snapshot records. No historical KalshiEdge final forecasts
are manufactured, and the comparison is limited to previously saved
KalshiEdge final records after SiddhaBot's artifact training date.
It does **not** replay all ~31,000 historical markets; doing so would
require a separate time-safe historical forecast by the then-active
KalshiEdge model.

The report lists excluded markets separately and does not count
unscorable missing-input markets as model prediction SKIPs. No profit,
execution, causal superiority, or out-of-sample training claims are made.

## Requirements and safety

- Previously saved KalshiEdge final forecasts and official settlements.
- Completed and verified Binance 1s archives covering those checkpoints.
- KalshiEdge research dependencies including XGBoost, NumPy, and
  PyArrow installed in `.venv`.
- The original artifact's immutable source Git blob must verify.
- **No credentials**, no orders, no model modification, no local data
  mutation except caching authenticated public model artifacts.
- Original prospective mode `-Mode Capture` remains available but is
  not needed for this historical command.

Automated check:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/bootstrap/test_siddhabot_historical.py
```
