# Full-journal AutoTrade evidence diagnostics — November 12, 2026

## Problem

An authenticated audit classified 30 local markets as 12 verified settled,
10 filled but unverified, 7 other unresolved and 1 no-fill. The 12 matched
markets produced +$0.1228 gross gains, $0.4906 gross losses and a
**partial** net -$0.3678. This is not the total account P/L.

The prior report did not distinguish:
- exchange settlement absent from the live API (unsettled **or** archived);
- settlement position mismatch or missing position fields;
- exchange fill quantity mismatch or malformed/duplicate fills;
- unrelated manual/account orders in the same market;
- order submissions with an unknown exchange response;
- exchange pagination/API errors.

## New read-only report

```powershell
cd "$HOME\Desktop\KalshiEdge"
$py = ".\.venv\Scripts\python.exe"
& $py -m kalshi_edge.trading.autotrade_profit_audit --live --details
```

The report includes `evidence_reason_counts` for *all* local journal
markets. `--details` opt-in adds `market_details` with ticker, side,
status, reason, journaled model confidence, available original quote price,
and realized dollars **only when reconciled**. The market details never
expose order IDs, client order IDs, fill IDs, credentials or private keys.

`--live` continues to use signed GET operations only. Offline use makes
no network calls or credential loads. No trading, staking, confidence,
threshold, execution or learning logic changes.

## Interpreting reason codes

- `VERIFIED`: full live settlement, net holdings and authenticated fills match;
  realized dollars include recorded fill fees.
- `SETTLEMENT_NOT_IN_LIVE_API`: not on the live settlement endpoint.
  **Not proof of an archive, a loss or a failed order.**
- `SETTLEMENT_NOT_FINAL`: matched settlement not conclusive.
- `SETTLEMENT_POSITION_FIELDS_MISSING` / `SETTLEMENT_POSITION_MISMATCH`:
  no valid proof of the final YES/NO holdings attributable to AutoTrade.
- `FILL_QUANTITY_UNRECONCILED` / `EXCHANGE_FILL_INVALID`: fill-level
  quantity or identity cannot be verified; never count as P/L.
- `OTHER_ACCOUNT_FILL_IN_MARKET`: account contains a fill outside the
  journaled AutoTrade stage; exact AutoTrade market attribution is unsafe.
- `ORDER_OUTCOME_UNKNOWN`: journaled submission may have been accepted;
  never assume a fill or retry it based on this diagnostic.
- `EXCHANGE_EVIDENCE_UNAVAILABLE`: API error, malformed response or
  incomplete cursor; neither win nor loss.
- `EXCHANGE_NOT_QUERIED`: offline report, no authenticated evidence.

Reason codes diagnose evidence gaps. They do not prove profitability or
authorize live model/risk changes. Kalshi publishes historical order/fill/
position endpoints separately and old archived fills may be absent from live
reads. Historical archive reconciliation is **not** included in this PR.

References:
- https://docs.kalshi.com/api-reference/portfolio/get-settlements
- https://docs.kalshi.com/api-reference/portfolio/get-fills
- https://docs.kalshi.com/getting_started/historical_data
