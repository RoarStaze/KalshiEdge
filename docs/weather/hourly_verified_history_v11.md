# WeatherEdge V11 — Bitcoin-style verified weather prediction history

**User-approved visual:** match the 2026-11-27 Bitcoin History screenshot with four summary cards, same 7-column table, color-coded outcome pills, refreshed timestamp and Show filter. Add Weather city filter and simple row paging.

**Authoritative data:** reuse the existing *first precommitted* one-per-city/event `weather_hourly_snapshots` and separate Kalshi-index-reconciled `weather_hourly_settlements` tables. Each saved forecast is the original prediction (never reconstructed from later readings). The existing V9 five-lead results remain on the Train page; they are **not** counted five times in the main History percentage.

**Durable correctness:** create a separate append-only, one-per-city/event SQLite verdict ledger within the weather worker, populated from the original frozen forecast+raw official index and exact Kalshi settled market. Backfill previously stored valid snapshot+settlement pairs using the same verifier. Replay source hashes/ticker/threshold/chronology on every read and fail closed if a stored verdict or source is tampered. A later change to a model cannot retroactively change recorded predictions.

**Definitions:** Forecast ABOVE = chosen YES probability above 50%; BELOW = below 50%; exact tie = SKIP. Correct and incorrect only follow a verified official YES/NO market result consistent with the saved Kalshi Index; SKIPs never count in the accuracy denominator. Pending means outcome absent or not yet audited; no missing outcome is counted wrong. Accuracy = correct / (correct + incorrect) (null for zero). Model probability = frozen probability for the chosen side; "confidence" = (2 × chosen-side probability − 1) as a labeled *derived model-strength score*, not a measured win rate. No invented weather 69% gate. City filter selects only its own events. Exact market close must come from the stored Kalshi listing; unknown closes display unavailable, never replaced with the index target hour. Verdicts are not trading P/L.

**UI/endpoint:** read-only `GET /api/weather/hourly/history?city=...&filter=...&limit=...&offset=...` with strict whitelist, full audit and paginated responses; synthetic preview returns an empty truthful history. Dashboard shows overall or city-scoped accuracy, correct/incorrect/skip/pending, one row per original forecast, latest first, and colored pills; no orders/credential dependency.

**Worker:** existing `kalshiedge start` 5-minute settlement poll calls verdict sync after settlement; no additional user commands. No changes to BTC/Gold, order placement, model inference, or physical weather training.

**Evidence limitations:** Official Kalshi Index and settled market agreement is source reconciliation, not independent sensor-level certification or proven future prediction skill. Historical verdict ledger begins with pre-existing saved forecasts and settlements; cannot invent past uncollected rounds.
