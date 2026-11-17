# Gold market result status audit — 2026-11-17

## Root cause of the reported zero YES/NO outcome count

The first Stage 2a inventory held 7,980 records / 7,484 unique `KXGOLD15M` markets and crossed the current/historical cutoff in 496 cases. Its first result counter incorrectly required `market.status == "settled"`, conflating the `GET /markets?status=settled` *query filter* with Kalshi's *response lifecycle statuses* such as `determined` and `finalized`. This can suppress genuine `market.result` observations.

The corrected collector counts distinct ticker YES/NO values independent of the lifecycle status but reports **finalized-only** results separately. Contradictory YES and NO observations for the same ticker are excluded from both counts and flagged. The report also shows counts **by raw record status** (which include cross-tier duplicates) and up to 20 example tickers with nonfinalized binary results or finalized records lacking a valid result.

These remain **observed Kalshi API values, not verified Pyth settlement labels**. A `determined` result is NOT assumed final. We do not train, trade, reconstruct historical Pyth data or change Bitcoin behavior.

## Inspect the original snapshot without re-fetching 7,980 records

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --inspect-inventory "data\gold\market_inventories\1794895835446578600-a660ce89c04184eb.json"
& .\.venv\Scripts\python.exe -m pytest -q tests/gold
```

This offline mode verifies the exact source identity, version, fixed 480-second checkpoint, 0.69 threshold and SHA-256 file prefix, then produces a concise summary. It leaves the immutable original artifact untouched, does not request any APIs and does not require API keys.

Use `--inventory` to create a **new** gold inventory after this fix, if needed. It is not necessary to re-download the previous snapshot to learn its status breakdown.

## Source and outstanding restrictions

- [Kalshi Get Market](https://docs.kalshi.com/api-reference/market/get-market): the market payload includes status, result, rules and close/settlement fields
- [Kalshi Historical Markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets): archived market entries
- [Kalshi Historical Data](https://docs.kalshi.com/getting_started/historical_data): separate archives and rolling cutoffs

Do not interpret the 496 duplicate records as conflicting outcomes (the previous report observed none under the old filter, which was incomplete). Do not approve training until exact Pyth index access, settlement timezone discrepancies and point-in-time eight-minute feature provenance are resolved.
