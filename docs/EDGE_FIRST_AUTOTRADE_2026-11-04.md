# Edge-First KXBTC15M AutoTrade (2026-11-04)

## Goal and limitations

Raise **net realized dollar profit**, not raw forecast accuracy, with the existing 480-second immutable final prediction. Every proposed entry must be fee-aware. **No guarantee of faster profits.** The reported 94% historical directional accuracy is not evidence of 94% fill-level profitability or calibration, and is **not used** as a universal win probability.

## What changes

- A real-money AutoTrade final BUY is **vetoed** if the fee-inclusive model EV gate fails. Previous versions deliberately ignored this gate. Minimum modeled net advantage is the maximum of $0.005 per trade, 2% of trade debit, and $0.03 per contract.
- The existing confidence is a **directional score**: estimated model-side probability = `(1 + confidence)/2`; the EV gate applies a three-percentage-point haircut. This is a heuristic, not independently proven calibration.
- Maximum budget is **5% of fresh available balance**, a conservative **quarter-Kelly** estimate, and the amount the user explicitly armed, whichever is smallest. A smaller balance reduces the stake; no Martingale progression. Budget is rounded **down** to $0.01 precision.
- On the first 480-second final attempt, AutoTrade prefers a **post-only limit order** joining the available best YES bid for UP, or the corresponding YES ask implied by the best NO bid for DOWN. Proposed price must be better than the live taker entry and pass the fee-aware EV check using the *taker* fee estimate as a conservative fee reserve.
- Maker orders are **exchange-expiring**, with an 18-second server-side expiration and local order verification after at most 14 seconds. An attempted cancellation must be confirmed against the exchange. An unknown order result stops AutoTrade; there is **never** an IOC retry on an uncertain or partially filled maker.
- Only after **verified zero fill**, a second attempt may use existing immediate-or-cancel execution. Before its signed POST, it re-reads account and book, rejects worsened prices and negative/insufficient estimated EV, and enforces the 5% balance ceiling again. Retries are limited by the existing 30-second 480-final entry window.
- Existing authenticated fill and settlement reconciliation remains authoritative for actual P/L, and every signed attempt is journaled before POST. Market changes, final SKIP, missing quotes, insufficient bankroll, and malformed prices never justify an unguarded order.
- Manual Buy/Sell is unaffected, and AutoTrade does not arm itself on startup.

## Risk caveats

- Quarter Kelly is **not truly optimal** until model probabilities are calibrated across current contracts and entry prices. A 5% cap still puts a substantial portion of a $3.54 account at risk over time.
- When the exchange cannot support small fractional positions or fee increments consume the supposed edge, orders skip. Forcing more fills would not demonstrate better performance.
- Maker orders may not fill. Different maker and taker fills introduce selection bias: **prospective fill/price/P&L data is essential** before concluding this increases profits.
- Fee estimates follow current local code `reference_nondirect_fee`; the exchange's actual fee and fills determine realized P/L.
- Robust account-wide daily stop loss and overall drawdown controls are **not** inferred from a balance snapshot: withdrawals, deposits and unsettled positions can cause false accounting. Existing 5%-per-entry risk cap is not a daily-loss cap. A separately reconciled account-level loss ledger is required to implement those correctly.
- A post-only order's exact order-status schema and exchange-side expiration behavior must be verified using live read-only API responses before treating trading as production-proven. No real orders are sent by CI.

## Operator runbook

1. Stop the local KalshiEdge server and ensure any existing exchange orders are known/closed. Do not update while a live order is outstanding.
2. Pull current main and restart (PowerShell):

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\kalshiedge.exe start
```

3. In the dashboard, explicitly arm the intended AutoTrade dollar **ceiling**. At $3.54 bankroll the 5% cap is about $0.17, and a smaller quarter-Kelly bound may apply. AutoTrade may skip.
4. Watch the AutoTrade History, quote rejection ledger, authenticated settled P/L, and `data/bootstrap/diagnostics/app-*.log` for maker order outcomes and skip reasons.
5. Do not claim improved profit from unit tests or backtested model accuracy. Compare actual prospective net profit per filled entry, effective quote fees, partial fills, and fill rates before promotion or risk increases.

## References

- Engine: `src/kalshi_edge/trading/armed_autotrade.py`
- EV and sizing: `src/kalshi_edge/trading/auto_edge.py`
- Maker planning and reconciliation: `src/kalshi_edge/trading/maker_entry.py`
- Signed Kalshi API and final fresh checks: `src/kalshi_edge/trading/manual_trade.py`
- Regression tests: `tests/trading/test_auto_value_first.py`, `test_auto_value_execution.py`, `test_manual_maker_value.py`, `test_maker_entry.py`
