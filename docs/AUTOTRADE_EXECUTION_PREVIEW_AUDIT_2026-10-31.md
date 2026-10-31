# AutoTrade execution-preview audit (decision-time evidence)

**Status:** Evidence collection only; existing model, sizing, confidence gates, order and retry policy unchanged.

## Why

The real order-stage journal previously captured ticker/side/stage/confidence/order outcome, but **did not archive the quote that passed the model-based value gate**. Without the original proposed price, estimated fees, shares, and expected net model value, retrospective comparisons to actual fills would silently reconstruct an offer that may not have existed when the order was submitted.

Kalshi documents authenticated fill fields including order ID, contract side, count, executed YES/NO price, maker/taker information and fee. These are the correct records for a later realized-execution join; this audit **does not fetch them**.

## Captured observations

For each accepted AutoTrade stage that reaches its existing **pre-POST** journal transaction, preserve:
- Quote response-receipt timestamp, ticker, stage, outcome and action (action implied by stage)
- Side's quoted price per contract, proposed share quantity, estimated total fee, and all-in estimated amount
- If BUY: model-implied selected-side win probability, estimated *modeled* net value and value-gate decision
- If reversal SELL: an explicit `SELL_UNGATED` label (a risk-reducing sell is **not** subject to the BUY-only value gate)
- Separately, for `NON_POSITIVE_EDGE`, `INSUFFICIENT_EDGE`, or `INVALID_QUOTE` BUY checks, an aggregate `(ticker,stage,reason)` count and latest sanitized quote. This **never creates an order attempt**.

The snapshots are allowlisted; the preview ID, API signing credentials, authentication headers, and complete exchange response are **not written**.

Existing sqlite journals migrate by adding nullable columns in-place on **application restart**. Historic stage rows remain NULL; the audit never backfills or fabricates old prices. Stage uniqueness and the original pre-order durable write are unchanged. Accepted quotes add no extra transaction before POST; rejected quotes are best-effort and logging failure cannot override a rejection.

## Run offline

After pulling latest `main`, with KalshiEdge disarmed:

```powershell
cd "$HOME\Desktop\KalshiEdge"
python -m kalshi_edge.trading.autotrade_execution_audit
```

This reads `BootstrapSettings.bootstrap_dir/data/autotrade_history.sqlite3` by default, and accepts `--db PATH` to inspect a specific journal. It does not call Kalshi, read private keys, or submit orders. The running app must be restarted **once** to begin collecting new accepted-stage quotes, because Python processes do not hot-reload their imported trading code. No restart is required merely to merge the GitHub PR.

The report counts attempted stages with and without archived quotes, rejected quote observations by reason, and labels realized profit **UNKNOWN**. To inspect actual fee-inclusive P/L, use the existing authenticated AutoTrade History reconciliation on the local app; that system remains separate from this research report.

## What this cannot establish

- A quote is not a fill. Even a favorable quote can be unavailable for the proposed quantity or move before the IOC request.
- Estimated fees come from sizing code, not a final exchange receipt; they must be compared with the recorded fill fee separately.
- Confidence-derived expected net dollars assume model probabilities are calibrated; they have not been independently validated as live trading probabilities.
- A rejection count represents sampled checks, not unique markets or opportunities.
- It does not record the continuously predicted probabilities for every second or every unarmed market; those are in the existing live prediction JSONL and final forecast artifacts.
- New monitoring must not automatically increase order size or loosen trading safeguards.

## Next research stage

Join the journal's immutable `(ticker, stage, order_id)` against authenticated `portfolio/fills` with action, side, ticker, count, effective execution price and actual fee, then compare to the archived quote at the **same** stage. Measure quote-to-fill latency/slippage, correct contract-side price conversion, realized net return and missing/unknown fill coverage, on a prospective time-ordered sample. Account for reversals and partial fills, and never select strategy thresholds using the same final held-out markets repeatedly.

## References

- Kalshi fills: https://docs.kalshi.com/api-reference/portfolio/get-fills
- Kalshi fee variation: https://help.kalshi.com/en/articles/13823805-fees
- Gneiting, Balabdaoui & Raftery (2007), *Probabilistic Forecasts, Calibration and Sharpness*: https://academic.oup.com/jrsssb/article/69/2/243/7109375
- Page & Clemen (2013), *Do Prediction Markets Produce Well-Calibrated Probability Forecasts?*: https://academic.oup.com/ej/article-abstract/123/568/491/5079498
