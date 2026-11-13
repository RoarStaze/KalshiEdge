# Direction-evidence audit for KalshiEdge — November 13, 2026

## Confirmed observation

The user's authenticated full local journal audit counted 31 markets:
12 verified, 11 `FILL_DIRECTION_CONFLICT` on DOWN fills, 4 unknown
submissions, 3 not submitted, and 1 no fill. Partial verified net P/L
remained **-$0.3678** after PR #135 corrected the V2 no/ask mapping.

The remaining 11 conflicts **have not been root-caused** by local
exchange-fill evidence. They might involve canonical fields, legacy
field disagreement, journal mismatch or other validation behavior.

Official exchange documentation:
- https://docs.kalshi.com/getting_started/order_direction
- https://docs.kalshi.com/api-reference/portfolio/get-fills

## New, diagnostic-only evidence

`autotrade_profit_audit --live --details` now optionally adds
`direction_evidence` on journal markets where strict fill validation
reports `FILL_DIRECTION_CONFLICT`. The entries contain only:

- `journal_action`: `BUY` or `SELL`
- `journal_outcome`: `UP` or `DOWN`
- `outcome_side`: `yes`, `no`, `MISSING` or `INVALID`
- `book_side`: `bid`, `ask`, `MISSING` or `INVALID`
- `legacy_side`: `yes`, `no`, `MISSING` or `INVALID`
- `legacy_action`: `buy`, `sell`, `MISSING` or `INVALID`

All fields are allowlisted fixed tokens. Rows are matched internally
on order ID but **never output** that ID or any raw exchange strings,
fill IDs, account information, credentials, fees or balances.
At most 8 matched fill records per journal market are displayed.
No unmatched/manual orders are included.

`--details` must be explicitly selected to print the market evidence.
The default report remains the bounded aggregate summary.

## PowerShell read-only examination

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.trading.autotrade_profit_audit --live --details | ConvertFrom-Json
$r.market_details | Where-Object { $_.pnl_reason -eq "FILL_DIRECTION_CONFLICT" } |
    ForEach-Object {
        $ticker = $_.ticker
        foreach ($e in $_.direction_evidence) {
            [pscustomobject]@{
                Ticker  = $ticker
                Journal = "$($e.journal_action) $($e.journal_outcome)"
                V2      = "$($e.outcome_side)/$($e.book_side)"
                Legacy  = "$($e.legacy_action)/$($e.legacy_side)"
            }
        }
    } | Format-Table -AutoSize
```

Use the observed enum pairs to decide whether the issue is a
deprecated-field discrepancy or a genuine opposite-side fill.
**Do not simply skip checks or recategorize trades as verified.**
All existing settlement counts, actual fill prices, fees, quantity,
attribution, and confidence/risk gates remain unchanged.

If an authenticated fill's canonical exposure is opposite to the
journaled trade, **pause AutoTrade** and investigate execution before
any new live bets. The audit itself sends signed GET requests only.
