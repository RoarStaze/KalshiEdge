# Correct Kalshi V2 DOWN fill exposure — 2026-11-13

## Verified root cause

Kalshi's official order-direction specification defines two *equivalent*
representations of the same directional bit:

| Canonical `outcome_side` | Canonical `book_side` | YES exposure |
| --- | --- | --- |
| `yes` | `bid` | +1 |
| `no` | `ask` | -1 |

Source: https://docs.kalshi.com/getting_started/order_direction

The previous `fill_reconciliation._scheme_exposure(..., flavor="v2")`
incorrectly assigned +1 to both `yes/bid` and `no/ask`. It also treated
`yes/ask` and `no/bid` as if they were valid exposures. This explained
why journaled DOWN fills could be rejected as
`FILL_DIRECTION_CONFLICT` even when Kalshi correctly recorded
`outcome_side=no, book_side=ask`.

The bug was reproduced in six **test-first** failures, including a
fee-inclusive 0.11-contract DOWN settlement, and corrected in the
validator. An inconsistent canonical pair must continue to fail closed,
reported as `FILL_DIRECTION_CONFLICT`. Legacy action+side is still
required to agree with the validated canonical bit when present;
partial identities, prices, quantities and actual fees are *not*
manufactured to recover a P/L number.

## Scope and safety

- Only fill identity **validation** and synthetic tests were changed.
- No new authenticated API operations, order submissions, or cancels.
- No changes to forecast logic, AutoTrade trading decisions, risk
  gates, confidence thresholds, training, or stake sizing.
- This does **not** prove the 11 real DOWN fills are all settled or have
  verified P/L. All other fill/fee/settlement quantity and whole-account
  attribution checks remain mandatory.

## Local verification

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.trading.autotrade_profit_audit --live --details | ConvertFrom-Json
$r.evidence_reason_counts | Format-List
$r.market_details | Where-Object { $_.pnl_reason -ne "VERIFIED" } |
  Select-Object ticker,outcome,pnl_reason | Format-Table -AutoSize
$r | Select-Object journal_market_count,verified_settled_markets,
  verified_net_pnl_dollars,complete_for_journal_markets | Format-List
```

**Interpretation:** A higher number of `VERIFIED` markets reflects
new accounting attribution, not better trading accuracy. Recompute
total net P/L from actual fills and settlements; never assume the
previous -$0.3678 partial number was the complete result.
