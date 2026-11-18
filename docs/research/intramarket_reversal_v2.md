# Intramarket reversal monitor v2 — evidence and limitations

This monitor is **read-only**. It does not modify the 8-minute immutable final forecast, live model probabilities, 69% final gate, auto-trade rules, or real-money execution. All alerts are provisional heuristic diagnostics, not validated short-horizon return probabilities.

## Live causal evidence

| Feature | Source | Interpretation |
| --- | --- | --- |
| Predicted-side probability drift | Existing 1 Hz bootstrap prediction records | Adverse changes in the deployed end-of-market estimate |
| Kalshi YES mid-price movement | Best bid and ask from the current market | Movement in *quoted contract valuation*, not a guaranteed fill |
| Kalshi quote spread | Best bid/ask | Illiquid or wide markets may be unsafe to scalp |
| Best-level YES bid vs YES ask queue sizes | Kalshi sequenced WebSocket order-book snapshots and deltas | Queue imbalance is a coarse microstructure proxy; not a complete order-flow measure |
| Signed 10-second taker volume | Existing Kalshi trade WebSocket and bounded live trade history | Recent YES- vs NO-initiated executed volume; **not** proof of who will win |
| BRTI price and distance from strike | Kalshi CF Benchmarks feed; checked timestamps | Underlying instrument pressure near the exact market strike |
| Binance BTC reference | Existing completed 1-second bars | Secondary, non-settlement reference; optional corroboration |
| Model component disagreement | Existing fitted model diagnostic | Multiple internal estimates disagree; source is **not** an independent market data feed |

Monitoring windows include 5, 15 and 30 seconds. BRTI and Binance fields are accepted only when causally timestamped and fresh. Broken sequence, stale observations, crossed quote or ticker mismatch cause an unavailable or reduced-evidence result. Historical records need not contain newly introduced optional fields.

## User-facing semantics

- **LOW/MEDIUM/HIGH** reflect a composite **stress index**, not empirical reversal probability. The 0–100 heuristic scale is **not** a probability and must not be promoted as one.
- **UP/DOWN** is a *pre-flip warning*: the model and Kalshi quote are still on the **current** side, and their recent directional drift points to the other side. When independent BRTI/depth/taker/secondary-price data exist, corroboration is included.
- **MONITORING** means no qualifying current pre-flip warning, **not** a prediction that a reversal will not occur.
- **UNAVAILABLE** is deliberate when recent evidence is missing or degraded.
- Display an evidence-quality indicator (**FULL/PARTIAL**) and the leading drivers, not a fictitious precision or scalp win rate.

## Evaluate on existing local prediction history

Use only the JSONL that has been captured locally. Nothing is downloaded from a user's account by the evaluator, and it cannot place orders.

```powershell
cd "$HOME\Desktop\KalshiEdge"
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.flip_replay "data\bootstrap\predictions\YYYY-MM-DD\predictions.jsonl"
```

The replay processes observations in recorded order and scores first warnings against an **observed future Kalshi YES quote midpoint crossing 50¢** within 15 seconds. It excludes incomplete future windows and feed gaps, deduplicates repeated warnings, and reports a Wilson 95% interval. That label is a **surrogate only**: it is not a verified executable sell price, a 15-minute settlement label, or after-fees profit.

### To demonstrate a genuine scalping advantage

Use held-out chronological market periods and a prospective shadow/paper stream with frozen thresholds. Separately test 5/15/30-second horizons, market regime, spread/liquidity, signal coverage, false alarms, warning lead-time, label quality, bid/ask executable exit simulation, Kalshi fee schedule, settlement, partial/no fills, and multiple trades per market. Never select thresholds on a lockbox and then report that lockbox as fresh evidence.

**Research background:** Cont, Kukanov & Stoikov (2014), *The Price Impact of Order Book Events*; Stoikov (2018), *The Micro-Price*; queue imbalance research by Gould and Bonart. These motivate microstructure features, but do not demonstrate predictive edge on this specific Kalshi contract.
