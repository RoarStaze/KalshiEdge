# 480-second BTC indicator strategy tournament (research only)

## Purpose

Evaluate several **fixed** indicator ideas from public TradingView community strategies against the exact outcomes that KalshiEdge forecasts: KXBTC15M decisions at 480 seconds before close. **This code does not install either MCP into KalshiEdge.** The research tools are discovery and external backtesting tools; production inference remains separately controlled.

## Strategy hypotheses

The standalone tournament implements independent mathematical scoring rules inspired by popular TradingView indicator concepts, but does **not** copy any contributor's Pine source:

- Strike distance only (settlement-reference baseline).
- Strike distance plus multi-horizon trend confirmation.
- Trend-plus-short-term pullback.
- Mean reversion against the recent move.

The completed public 60s/300s returns, their availability flags, 60s realized volatility, and the normalized checkpoint-time strike distance are pre-existing KalshiEdge features. Source timestamps and the original forecast SHA-256 are verified before any saved feature enters an evaluation.

**Public research references, not verified Kalshi winning strategies:**

- TradingView's public [BTC EMA/RSI strategy](https://www.tradingview.com/script/iR2tIMy1-BTC-Trend-EMA-Crossover-RSI/), and [BTC trend/ADX pullback](https://www.tradingview.com/script/RI4hNH51-Anwar-BTC-Trend-Strategy-V2-Fixed-ADX-Working/).
- The [TradingView indicator-search MCP](https://github.com/daviddme/tradingview-indicator-search-mcp-server) searches and retrieves public Pine source. It requires an installed Node 22.5+ client; the MCP is **not connected** here.
- [TradingKit Research Workflow](https://kb.tradingkit.com/getting-started/research-workflow/) recommends baseline, bounded changes, walk-forward and prospective validation. TradingKit's authenticated backtest tool was **not accessible** in this environment, so **no TradingKit results are claimed**.

## Backtest design

1. Load only immutable 480s final records, SHA-bound point-in-time forward-feature snapshots, and independently recorded official Kalshi settlements. Invalid, old, missing, stale, future or unlabeled records are excluded. No label exists inside a prediction snapshot.
2. Partition chronologically into 60% training, 20% validation, and 20% untouched holdout; require at least 240 paired markets over 5 UTC dates before reporting a comparison.
3. Select a two-strategy shortlist based on earlier training-period probability scores. Only outcomes known before the validation period count as training evidence. Choose a candidate on validation only when Brier, log loss, 69% qualified accuracy, and 69% decision coverage all strictly improve over the same-market incumbent.
4. Evaluate only the previously selected winner against the incumbent on the later untouched test. **No candidate is promoted automatically.** Positive historical metrics remain insufficient evidence of profitable execution.
5. Preserve the existing coin-flip veto for both candidate and incumbent scorecards.

Run locally after the GitHub update:

```powershell
cd "$HOME\Desktop\KalshiEdge"
.\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.strategy_tournament_480
```

**Missing evidence:** No authenticated TradingKit MCP access, no user-local historical feature snapshots in CI, and no recorded, executable after-fee fills for selected candidate trades. **The tournament does not identify a proven winning strategy, does not verify net profitability, and does not change AutoTrade or the live model.** If the report has insufficient evidence, continue collecting settlement-linked 480s snapshots. A separate future deployment proposal must demonstrate actual paper/live execution economics before any money is risked.
