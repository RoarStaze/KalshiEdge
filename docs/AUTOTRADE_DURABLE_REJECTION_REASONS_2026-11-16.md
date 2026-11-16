# Persistent AutoTrade non-submission diagnostics — November 16, 2026

## Root cause and user impact

The live, **final-only** KXBTC15M AutoTrade may show a locked eligible
UP/DOWN forecast but fail to submit because a **fresh** cash check, fee-inclusive
user dollar budget, executable quote, preview expiry or definitive exchange
rejection invalidated the order. Previously it displayed the text only in the
ephemeral armed-state UI, while the durable SQLite stage journal saved only
`NOT_SUBMITTED`. When continuous AutoTrade advanced to another market or
the application restarted, the **specific rejection cause disappeared**.

This made the earlier fixed **5% cash ceiling** incident unnecessarily hard
to diagnose and made a verified profitable-looking forecast easy to confuse
with a filled, profitable trade. A predicted win or modeled expected value
is not a confirmed exchange fill.

## Fix implemented

The final-only AutoTrade engine persists a restricted **rejection reason
code**, never raw exception text, for each journaled `NOT_SUBMITTED` stage.
Examples:

| Safe code | Interpretation |
|---|---|
| `INSUFFICIENT_CASH` | New fresh balance cannot afford the fee-inclusive debit |
| `DOLLAR_BUDGET_EXCEEDED` | Latest quote exceeds the exact amount armed by the user |
| `QUOTE_EXPIRED` | Original preview token expired before execution |
| `PRICE_MOVED` | A strict price-change check rejected a stale quote |
| `MARKET_CHANGED` / `MARKET_NOT_OPEN` | Ticker/market became ineligible |
| `FRESH_QUOTE_READ_FAILED` | Fresh account/quote HTTP read failed |
| `FRESH_QUOTE_VALIDATION_FAILED` | Safe pre-submit input validation failed |
| `QUOTE_IDENTITY_CHANGED` | Fresh quote identity differed from preview |
| `AMOUNT_BELOW_EXECUTABLE_MINIMUM` | Stake cannot buy an allowed share increment |
| `NO_EXECUTABLE_LIQUIDITY` | Missing executable price or liquidity |
| `EXCHANGE_REJECTED` | Definitively rejected Kalshi HTTP client error |
| `OTHER_DEFINITE_REJECTION` | Definitive non-accepted outcome; cause not allowlisted |
| `LEGACY_CASH_FRACTION_CAP` | Old opt-in percentage-cap path, if invoked |

The new SQLite `rejection_reason_code` column is migrated **non-destructively**.
Old ledger rows remain unchanged and may report
`OLDER_JOURNAL_REASON_UNAVAILABLE` because the old exception was never saved.

All uncertain exchange outcomes stay `UNKNOWN`: **they do not gain a false
definite cause or become safe for an automatic retry**. Only exact allowlisted
codes reach SQLite and AutoTrade History; raw HTTP/error bodies, auth tokens,
client IDs and sensitive preview identifiers are never serialized to this
field. The UI uses `textContent`, not HTML insertion.

The existing read-only execution audit gains:
- `unsubmitted_stage_attempts`: count of recorded stages marked NOT_SUBMITTED.
- `unsubmitted_reason_counts`: counts by safe diagnostic reason.
- A fallback for older read-only journals lacking the new column.

These are **journaled attempts only**, not proof AutoTrade was armed in every
market. A market with no stage can indicate not armed, SKIP, no quote, market
change or other earlier gate, so this patch does **not** label missing stages
as a failed exchange order.

## Windows PowerShell update and diagnosis

```powershell
cd "$HOME\Desktop\KalshiEdge"

# Preserve any uncommitted local files before updating.
git status --short
git switch main
git pull --ff-only origin main

# Restart KalshiEdge normally, and arm AUTO again with the intended dollar amount.

$py = ".\.venv\Scripts\python.exe"

# Safe offline diagnostic; no keys, Kalshi requests or orders.
& $py -m kalshi_edge.trading.autotrade_execution_audit

# Optional regression test of the new local stage schema and cause handling.
& $py -m pytest -q tests/trading/test_autotrade_durable_rejection_codes.py

# The frozen new-market research evaluation remains as it was.
& $py -m kalshi_edge.bootstrap.prospective_error_monitor_480
```

In the AUTO tab, see the persistent stage line for each historical market.
For *new* definitely rejected stage submissions after this update, the line
will show a human-readable reason such as
`FINAL BUY UP: NOT SUBMITTED (INSUFFICIENT CASH)`.
Legacy rows without saved cause cannot be retroactively reconstructed.

## Non-goals and safeguards

This patch does **not** change model predictions, 480-second forecast
checkpoint, >63% predicted-side gate, near-strike BRTI flip veto, AutoTrade
arming, selected amount, fees, quote validation, exchange order payloads,
the user-selected stake change from PR #175, the two-bounded-NO_FILL retry
rule or unknown-order idempotency protections.

The independent frozen 480-market prospective validation manifest remains
untouched. Live accuracy/coverage, fee-adjusted trading profitability, and
fill success cannot be inferred from a stored rejection code. This is a
targeted operational diagnostic fix; prospective evidence must still be
used before modifying any money-moving rule.
