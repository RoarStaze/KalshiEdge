# WeatherEdge V6 — independently refreshed hourly market quote evidence

## User outcome
Keep the hourly temperature forecast simple while showing the **real Kalshi YES/NO asking prices, available quoted sizes and freshness** for an exact matching contract. Never mistake model confidence for a market price. Never recommend a bet using a missing/stale quote.

## Verified external contract
Kalshi public GET /markets/{ticker} returns an individual Market object, current status and close_time, and optional yes_ask_dollars/no_ask_dollars with corresponding fixed-point ask sizes. API documentation: https://docs.kalshi.com/api-reference/market/get-market . Availability, quote, settlement and model forecast are distinct. Market quotes can change after retrieval.

## Safety model
- Require a preexisting official HS-index market matched by `hourly.py` (do not manufacture a ticker).
- Recheck the exact ticker through GET /markets/{ticker}; verify event ticker, series, binary type, threshold, active/open status and real closing timestamp.
- Decimal-parse fixed-point dollar asks and positive fixed-point sizes. Missing/zero sides do NOT become buyable offers.
- Persist source timestamp and digest for audit; never call order placement. Never infer odds by subtracting NO bids, and never use old `last_price` as executable ask.
- Quotes are fresh for 35 seconds maximum; stale quotes must lose price/decision eligibility. Distinguish `NO_VISIBLE_ASK`, `QUOTE_PENDING`, `STALE_QUOTE`, `CLOSED`, `QUOTE_UNAVAILABLE`.
- Fetch at most two nearest strikes per city every 20 seconds via an independent collector, safely separated from core official-index collection.
- Do not compute a "guaranteed edge" from unvalidated forecast probabilities. UI labels: "Kalshi asking prices", "Model probabilities (unvalidated)", no claim that theoretical price gap accounts for fees/fill risk.

## Interfaces
- New `src/kalshi_edge/weather/hourly_quotes.py` isolates parsing, storage, read time checks and bounded independent collection.
- `hourly.py` overlays quote provenance onto matching market projections and rejects a fresh confirmed closure.
- `runtime.py` manages the independent 20-second worker and reports its health.
- `hourly_ui.py` shows separate model chance and verified ask/size, with clear pending/expired status; no trading button.

## Verification
Synthetic tests verify identity and threshold, exact close, decimal bounds, timestamps, no liquidity, corrupt/stale cache, collector isolation, UI markup and no orders. Full repository tests + container build must pass before merging. A successful unit test does not establish a currently open or profitable weather trade.