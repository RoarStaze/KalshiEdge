# AutoTrade DOWN realized P/L reconciliation repair — 2026-11-04

## Root cause
- `src/kalshi_edge/trading/manual_trade.py:quote_from_book` issues **BUY DOWN** as an **ASK on the YES book**, and BUY UP as a BID on YES. SELL DOWN corresponds BID on YES; SELL UP corresponds ASK on YES.
- `src/kalshi_edge/trading/autotrade_pnl.py:_calculate` currently requires `fill.action='buy'` and `fill.side='no'` for a recorded BUY DOWN; it does not recognize the equivalent `outcome_side='yes', book_side='ask'` of the actual V2 order. It also requires `no_price_dollars` with no complement fallback. Thus a genuine authenticated DOWN fill can be incorrectly rejected as UNAVAILABLE, despite successful journal and settlement.
- `src/kalshi_edge/trading/execution_quality.py` repeats the same fragile old-fields match and price selection. Kalshi's authoritative `GET /portfolio/fills` endpoint advertises `outcome_side`, `book_side`, `yes_price_dollars`, `no_price_dollars`, `fee_cost`, `count_fp`, plus legacy `side` and `action`. Docs: https://docs.kalshi.com/api-reference/portfolio/get-fills
- We cannot inspect the user's authenticated fills here: matching the observed card to a specific fill rejection remains unverified until runtime. No P/L guesses.

## Fixed semantics and safeguards
- Represent each **journaled** economic action as signed exposure in YES (+ or -):
  - BUY UP = + YES; SELL UP = - YES
  - BUY DOWN = - YES; SELL DOWN = + YES.
- Derive the same exposure from V2 `outcome_side` and `book_side`:
  - YES/BID or NO/ASK = + YES
  - YES/ASK or NO/BID = - YES.
- Derive exposure from legacy `side` (yes/no) and `action` (buy/sell); both schemes must agree if both present, otherwise **fail closed**. A lone incomplete scheme is insufficient.
- Require immutable journal order IDs, market ticker, exact 2dp fractional fill quantities, actual source fees; match counts and all fills and reject any unrelated manual fill or duplicate.
- Use *real* `yes_price_dollars` and/or `no_price_dollars`; if one is absent, derive its binary complement from the other, with Decimal exact arithmetic. If both present, require their sum to be exactly $1 to fixed-point tolerance. Never invent a fee or fill. DOWN cost = executed NO price; YES cost = executed YES price.
- Share this one validation/helper with both AutoTrade P/L and execution quality, instead of having quote-to-fill reject valid V2 DOWN order. No change to order placement, AutoTrade, amount, confidence or market forecasting. UI continues to show P/L unavailable when no trustworthy reconciled fill/settlement exists.
- Add regression tests: BUY DOWN win/loss on V2 YES ASK and NO BID, incomplete prices, reciprocal prices, both legacy and V2, mismatch side, missing fee, no fills, mismatch quantity, unmatched manual fills, quote quality.

## TDD and merge gate
1. Tests on branch before production edits, verify RED on current CI.
2. Implement tiny shared read-only helper and replace the exact old-only identity/price checks.
3. Verify FULL current-head CI including trading tests and production container build, review diff.
4. Merge PR only when green; user updates local repo with `git pull --ff-only origin main` and restarts app. The specific user's filled DOWN P/L remains unverified until their authenticated records reconcile.
