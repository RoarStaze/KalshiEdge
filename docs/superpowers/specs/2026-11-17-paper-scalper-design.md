# Paper Scalper Design — KalshiEdge BTC 15m

## User outcome
The user explicitly chose to implement the paper-mode stage before real-money scalping. Build a separate, durable simulation that:
- Reads current public executable Kalshi order-book evidence and current locked final forecast.
- Finds fee-aware intramarket research entries, models fills with pessimistic slippage and sufficient top depth, and manages exits before settlement using the research policy.
- Optionally re-enters the same market after a cooldown and another independent qualifying signal.
- Records every paper round trip in SQLite, including buy/sell prices, modeled fees, exit reason and net simulated P/L, and carries an open position through application restart.
- Is separately enabled from existing real-money AutoTrade, with a distinct paper-only interface and read-only status.

## Verified repository constraints
- Existing live `ArmedAutoTrader` supports one final buy per market. Never change it or its >69% final gate.
- `scalp_policy.py` yields research `RESEARCH_BUY_SIGNAL`, `STOP_LOSS_SIGNAL`, `TAKE_PROFIT_SIGNAL`, `TRAILING_STOP_SIGNAL`, `TIME_EXIT_SIGNAL`, and blocked exits; **never** live order authorization.
- Kalshi public order book returns YES/NO bid levels; use `quote_from_kalshi`. Position sizing uses fee-reserved executable asks and hypothetical taker slippage. An early exit uses executable bids minus pessimistic slippage and fee reserve, never midpoint.
- No historical proof of profitable short-horizon scalp trading exists; simulation performance is NOT a live return forecast.

## Paper lifecycle
- Persistent SQLite `paper_scalp.sqlite3` has one OPEN position maximum, multiple independently numbered cycles and completion records, a unique OPEN index, entry/exit modeled fee reserves, and a coherent virtual bankroll.
- After restart, **new entries are disabled**, but the open paper position remains and is monitored for exits regardless of disabled new entries. Pausing freezes entries, not exit protection.
- A final ABOVE/BELOW with matching ticker and scheduled checkpoint plus strict directional probability >.69 is required for every new paper entry. Fail closed on missing data, stale prediction/quote, closed market, or unknown lock. Hard cap three entries per market and a 10-second re-entry cooldown.
- While exposure exists, it must be managed even if the current live market changes. Attempt public quote for the owned ticker; if no valid/available book, retain OPEN with explicit unresolved risk, never treat the stop as executed.
- When an official saved and verified settlement becomes available, close the simulated position to modeled binary payout. Without settlement proof, it remains OPEN_UNVERIFIED and prevents additional paper entries.
- Virtual bankroll defaults to $100; per-entry budget defaults to $1, capped at 5% of available virtual cash. Daily simulated loss budget $2, pause entries after three losses. Slippage is 1¢ per contract *on both sides*. No optimistic paper partial fills: hypothetical fill requires full visible top-of-book depth and a fresh observation.
- Treat fixed-dollar risk thresholds as research hypotheses only; no claims of optimality or guaranteed stop.

## Interface and verification
- Separate same-origin `/api/paper-scalp/status`, `/api/paper-scalp/arm`, `/api/paper-scalp/pause` routes, no Kalshi credentials required, no calls to signed order clients.
- Trade page AUTO section displays a clearly labeled PAPER SCALPER panel with its own enable/pause button, budget and simulated P/L/history.
- Startup worker polls independently, stop closes only its *own* read-only public client and does not modify other services. Existing PAPER_ONLY locked-final trader and real-money AutoTrade remain untouched.
- Offline deterministic tests prove multiple cycles, early take-profit, stop, no-liquidity blocking, manual pause still monitoring open paper risk, restart, gate, daily stop, same-origin POST, and no real exchange order submission.
- CI must pass full tests and production build before merge to main.

## Deployment
Research-only, independent paper simulations. Live scalping remains DISABLED. Model fees and hypothetical fills can be materially different from real execution. No live-order permission or guaranteed stop-loss.
