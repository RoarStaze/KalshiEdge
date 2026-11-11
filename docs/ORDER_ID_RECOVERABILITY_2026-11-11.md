# Durable AutoTrade client order IDs — November 11, 2026

## Defect and evidence

The live AutoTrade write-ahead SQLite stage journal was committed before order
submission, but `ManualTrader` generated a new `client_order_id` later. If
Kalshi accepted a POST and its reply was lost, the journal preserved UNKNOWN
without the exact client identity for read-only order discovery.

The offline test-first GitHub Actions run for commit
`f8cbd575fb679ca77965400e4e1544d75a262960` failed exactly two new
tests (missing `client_order_id` column and missing legacy-stage field);
all 1,023 pre-existing tests passed.

## Minimal change

For explicitly armed production `ManualTrader` AutoTrade BUYs, generate one
UUID before the stage journal transaction, store it atomically with the
existing intent/quote and pass the identical UUID into the maker POST or
fallback IOC POST. Preserve existing manually placed orders' ordinary UUID
generation. Existing databases migrate non-destructively: pre-change stage
rows retain a null client ID rather than pretending the remote ID is known.

No risk thresholds, forecast policy, fees, staking, retry counts, order types,
or manual trading behavior are intentionally changed.

## Verification

- A mocked exchange reads its SQLite stage row from a separate connection at
  POST time, compares the committed ID with the actual outbound order ID,
  then simulates a lost exchange response. The engine must remain UNKNOWN,
  preserve the ID through restart and never resubmit the round.
- A legacy SQLite stage table must migrate without losing an UNKNOWN row.
- A profitable maker attempt must use the ID saved in its stage journal.
- All other existing tests and CI must remain green before merging.

## Remaining limitations

This is *identity provenance*, not automated post-crash reconciliation.
Existing historical UNKNOWN rows have no recoverable client ID from this
migration. A separate authenticated, paginated, read-only order/fill
reconciliation procedure is still required. This change demonstrates no
improvement to realized net profit or forecasting accuracy.
