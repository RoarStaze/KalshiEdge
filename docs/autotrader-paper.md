# KalshiEdge AutoTrader — paper-first design

## Scope (this PR)

A separate, loopback-only paper-trading companion that reads **immutable V5
`final_forecasts`**. It never calls an authenticated Kalshi order endpoint.
It does not modify the existing final forecast gate, model, V6/V6.1
prospective records, settlement files, or model artifacts.

### Launch on Windows

With KalshiEdge installed in the same virtual environment, run the main
predictor and history dashboard as usual:

```powershell
kalshiedge start
```

In a **second PowerShell terminal**, at the same project root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m kalshi_edge.trading
```

Open **http://127.0.0.1:8898/**. Enable paper trading explicitly. The
paper engine always starts disabled after restart. Leave both processes
running to capture the 240-second locked forecast and official settlements.

The website has a local credential form. Pasted API ID + unencrypted
RSA/Ed25519 PEM are validated and encrypted with **Windows DPAPI** tied to
your Windows user, saved under
`%LOCALAPPDATA%\KalshiEdge\autotrader-credentials.dpapi`. They are not
written to the repository, browser localStorage, or the paper-trading ledger.
The paper service **does not use credentials to place orders**. You can delete
the saved credentials through the page. Because the form runs over local
HTTP, do not expose the port through tunnels, remote bindings, or proxies.

### Deterministic entry eligibility

- Only ticker prefix `KXBTC15M-` and final forecasts `ABOVE` or `BELOW`.
- Exact V5 immutable checkpoint within two seconds of `close - 240s`.
- No trade later than 20 seconds after that recorded checkpoint.
- Require original V5 directional probability at least 84.5%, matching the
  immutable confidence gate (69% scaled confidence).
- Confirm matching market ticker, strike, close time, active status, and
  fractional-contract support from Kalshi's public API.
- Derive best BUY ask from the **opposite** bid side of the `orderbook_fp`.
  For YES/ABOVE ask = 1 - best NO bid; for NO/BELOW ask = 1 - best YES bid.
- Round quantity DOWN to 0.01 contract granularity, never crossing total
  **hypothetical** debit of $0.10 including fee reserve.
- General taker-fee research estimate = ceil-cent(0.07 × price ×
  (1-price) × quantity), plus $0.01 rounding buffer. **Not a verified
  market-specific fee**; insufficient for enabling real orders.
- Require modeled net expected profit at least $0.005 per order *after the
  estimated fee*. If the executable price cannot pass that filter, SKIP.
- Exactly one attempt per market, even on restarts. SQLite persists each
  attempt before external market-data calls; unknown attempts are not retried.

### Loss controls

- Maximum **paper** debit per market: $0.10 including fee estimate.
- Daily **paper** loss budget: $0.50, including all outstanding worst-case
  open debits and losses already realized on the UTC day.
- Pause after 3 successive settled paper losses.
- No martingale, averaging down, automated early exit, late lock, or
  position reversal.
- Loss budgeting limits new trades; it cannot guarantee a fill, execution
  price, or profit. An early-exit/stop-loss algorithm is deliberately not
  enabled: spreads/fees and fleeting liquidity may make it worse than
  a capped maximum loss followed by settlement.

### Ledger and P&L

A paper ledger is created at
`data/bootstrap/trading/paper.sqlite3`. The website displays simulated
open positions, skips, and settlement-driven paper P&L. Only independently
verified saved Kalshi settlements from `final_history.py` determine final
outcomes. Paper prices assume instantaneous fill at the displayed best
opposite bid and therefore may be optimistic. Fee reserves are estimated,
not measured. Reported paper P&L is **not actual realized trading P&L**.

## Paths to actual live orders (future separate review)

1. Validate accurate per-series fee schedules, tick-size grid, available
   fractional liquidity, and exact worst-case cash debit including rounding.
2. Implement a separate authenticated Kalshi client supporting both
   RSA-PSS and Ed25519. Do not log or return credentials.
3. Add durable client-order IDs and request-before-send journal with
   reconciliation against `GET /portfolio/orders`, fills, and positions;
   after uncertain responses, fail closed without repeat submissions.
4. Place only bounded `immediate_or_cancel` V2 orders at checked ask,
   with explicit manual arming, market/account kill switches, and separate
   demo and production environments.
5. Verify fills and actual fees against account history; decide whether a
   reduce-only emergency exit is preferable to holding after *independent*
   out-of-sample paper evaluation.
6. Test executable behavior in Kalshi demo; keep live disabled until a
   separate manual approval.

**No guarantee of a profitable strategy.** Market win rate is not net return;
unverified probabilities, spreads, fees, and fractional rounding can all
make positive-looking forecasts unprofitable.

### Reference documentation

- [Official Kalshi V2 order API](https://docs.kalshi.com/api-reference/orders/create-order-v2)
- [Official fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration)
- [Official market orderbook API](https://docs.kalshi.com/api-reference/market/get-market-orderbook)
- [Official fee rounding](https://docs.kalshi.com/getting_started/fee_rounding)
- [Official request authentication](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests)
