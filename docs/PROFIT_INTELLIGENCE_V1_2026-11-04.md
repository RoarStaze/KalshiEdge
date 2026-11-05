# Profit Intelligence V1 — implementation and operating limits (2026-11-04)

## Purpose

The purpose of this release is to make **modeled profitability, execution routing, forecast reliability and verified dollar profit visible together** for the existing eight-minute KXBTC15M AutoTrade system. It is **not** a claim of faster realized earnings.

### Live AutoTrade behavior

1. The original 480-second immutable final forecast, 69% strength threshold, active near-strike coin-flip veto, authenticated manual authorization, continuous arming semantics, short 30-second entry window and stale-feed refusal remain unchanged.
2. At a fresh quote, compare the taker estimate against an independently inspected, size-bounded maker opportunity even when the taker already has positive modeled EV.
3. If the maker plan passes the same fee-aware edge calculation, prefer the maker route with remote order expiry, terminal-status reconciliation, and no ambiguous retries. When no qualifying maker quote is present, route an approved taker estimate straight to the independently refreshed IOC path. If neither qualifies, **do not submit**.
4. All BUY paths still check fresh account balance, current market and quotes, the user's authorization ceiling, and the hard 5%-of-cash risk cap; unknown order outcomes stop trading. Neither maker nor taker can bypass the existing per-contract fee-aware EV minimum.
5. The AutoTrade status response includes `latest_opportunity` describing the most recent evaluated quote route, raw modeled win probability and estimated net EV. These fields are **not** a guaranteed return or actual fill. They reset on a new market.

### Profit Intelligence evidence

`GET /api/armed-trade/history` now includes a separate `profit_intelligence` object:

- **Forecast**: all distinct, officially settled, point-in-time 480-second probability observations including SKIPs; sample count, Brier score, log loss, coarse calibration bands, conditional issued-direction accuracy, matched market-midpoint Brier and near-strike SKIP counts. Corrupted/non-complementary probabilities, duplicate tickers and legacy 240-second observations do not enter these metrics.
- **Quoted economics**: a separate one-contract *gross* hypothetical sum over directional records with valid contemporaneous top-of-book. It is explicitly **not** a real quote-sized fill or after-fee return.
- **Execution**: recent AutoTrade journal's exchange-verified settled net P/L from authenticated fills and official settlement; unresolved fills stay unknown. A descriptive dollar-per-observed-clock-hour figure is exposed only when the recent window has at least two verified fills, all filled records are reconciled, every timestamp is valid, and the observed wall-clock span is at least one hour.
- **Promotion**: `ready_to_change_live_model` remains `false`. There is no causal evidence that the calibration bands represent independently deployable probabilities or that a challenger improves actual net profitability. Historic scores never silently promote a model or raise the bankroll cap.

The Trade page's Auto tab displays the observed current quote route (while available), the 480s Brier sample size, the existing exchange-verified P/L, and descriptive net/hour only when calculable. The user-facing text explicitly distinguishes modeled EV from settled money.

### Run the report locally (PowerShell)

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.trading.profit_intelligence
```

The CLI is deliberately offline/read-only: it never sends Kalshi orders, fetches authenticated fills or invents unverified exchange profit. Exact settled trade P/L is available through the running app's authenticated AutoTrade history reconciliation.

### What V1 does NOT establish

- Model probabilities in the EV gate currently use `(1 + confidence) / 2 - 0.03`, a conservative but **uncalibrated** heuristic. Brier/log-loss on previously collected markets is descriptive; promotion must require new, independently held-out chronological evidence.
- A maker's lower limit price is not a guaranteed fill. An IOC may lose its EV before the exchange receives it; the production pre-submit checks remain mandatory. Quoted market midpoint, maker passive order, and filled taker orders are not interchangeable.
- No new automatic ETH/SOL/Gold trading, early-entry scalping, dynamic risk increase, assumed maker fill-probability optimizer or unvalidated profitable-exit policy is enabled.
- No continuous-profit guarantee, audited whole-account return, nor maximum drawdown statistic exists yet. The existing recent journal excludes manual/other-account trades and can have delayed settlements.

### Remaining experimental gate for any higher-risk rollout

Register a frozen candidate, store event-time bid/ask/depth and each maker/IOC fill or non-fill *before* settlements, compare at identical opportunities over multiple volatility regimes, use independent future chronological markets and report actual net P/L, fill probability, cost, return, drawdown and statistical uncertainty. Promote only after the measurable improvements survive these gates.

### Files

- Live route: `src/kalshi_edge/trading/armed_autotrade.py`, `src/kalshi_edge/trading/manual_trade.py`, `src/kalshi_edge/trading/maker_entry.py`
- Read-only evidence: `src/kalshi_edge/trading/profit_intelligence.py`
- UI: `src/kalshi_edge/bootstrap/manual_trade_ui.py`
- Tests: `tests/trading/test_profit_intelligence.py`, `tests/trading/test_profit_routing.py`
