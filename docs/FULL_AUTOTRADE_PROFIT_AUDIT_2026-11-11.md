# Full local AutoTrade P&L audit — 2026-11-11

## Scope

Read every market from the existing local SQLite AutoTrade journal instead of
the last 25–100 records shown by the trading dashboard. This is explicitly
NOT an all-account ledger: manual orders, pre-journal history, missing old
exchange settlements, credits, transfers, funding, and deposit activity are
not attributed to AutoTrade.

The command is read-only: no changes to the journal, no AutoTrade settings or
model change, no buy/sell/cancel calls. Its default mode makes **no network
requests** and does **not** load credentials. Only `--live` activates signed
GETs to `/portfolio/settlements` and `/portfolio/fills`.

```powershell
cd "$HOME\Desktop\KalshiEdge"
$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.trading.autotrade_profit_audit
& $py -m kalshi_edge.trading.autotrade_profit_audit --live
```

## Strict verification

A settlement is eligible for exact realized P/L only when:
1. Its conclusive YES/NO result is present.
2. The settlement's fixed-point YES and NO holdings exactly match the
   holdings reconstructed from fully accounted AutoTrade stages.
3. Every journaled filled order's quantity matches authenticated fill records.
4. All matching account fills on the market belong to the journaled orders,
   with valid side/action, fixed-point price and actual charged fee.
5. Every required API cursor has been completely traversed.

Trade fees are deducted from actual fills. A gain or loss is *verified* only
for matched markets. Missing API records are not a loss of zero dollars.
For histories above 100 market tickers, fill GETs are partitioned into
groups of at most 100, the documented exchange limit.

## Known limitations

The settlement endpoint only has live settlement rows. Older archived markets
use `/historical/positions` with different fields, so this command does NOT
claim those historic markets as verified. The CLI does not yet reconcile
historical-market account positions or fees across the archive cutoff.
A `COMPLETE_FOR_LOCAL_JOURNAL` status is only about the provided local
journal and all matching available live exchange evidence, not lifetime or
whole-account profitability.

References:
- https://docs.kalshi.com/api-reference/portfolio/get-settlements
- https://docs.kalshi.com/api-reference/portfolio/get-fills
- https://docs.kalshi.com/getting_started/historical_data
