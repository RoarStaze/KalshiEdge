# Matched AutoTrade quote-to-fill execution-quality research plan

> **For agentic workers:** Required skills: superpowers:test-driven-development and superpowers:verification-before-completion. Follow failing tests, verified green CI, then merge.

**Goal:** Measure adverse slippage and actual paid fee differences per filled contract by matching newly archived stage quote previews against Kalshi authenticated fills.

**Architecture:** Pure `trading/execution_quality.py` calculator accepts stage records (ticker, order, quote side price, quoted count, fee estimate) and official fill records. `autotrade_pnl.enrich_history` already verifies fills and settlement before publishing P/L; add `execution_quality` only after successful reconciliation, using the same fetched fills, with no additional Kalshi requests or changes to signed POSTs.

**Tech stack:** Python >=3.12, stdlib Decimal, pytest, existing trade journal and reconciliation.

**Safety:** Never change AutoTrade thresholds, order sizing, arming, sell reversals, fees or prediction model. No new network calls or executable decisions. On unmatched, invalid or pre-PR67 missing quote, do not claim that execution quality is measured. Real fees are read from the same trusted fills. An apparent positive quoted edge is not future profitability.

## Acceptance tests

- [ ] BUY quote 40c plus 0.006 estimated fee, filled 43c plus 0.007 actual fee, on 0.25 share: 0.034 adverse per share and 0.0085 on filled quantity.
- [ ] SELL reversal with lower filled sale price and higher fee: positive adverse cost, preserving BUY-only value gate.
- [ ] Partial-fill quote-size normalization; avoid counting unfilled quantity as executed.
- [ ] Weighted average across multiple fills; correct fill sides, actions and ticker.
- [ ] Missing old quote emits MISSING_QUOTE; does not change independently verified P/L.
- [ ] Conflicting fill or wrong ticker: no quality or fake realized net.
- [ ] Existing reconciliation remains order-ID and settlement scoped; no repeated signed API POST.
- [ ] Full repository CI passes before merging.

**Interpretation limits:** These metrics describe observed execution relative to the recorded estimate for *filled* orders only and are subject to selection bias. The first preview may differ from final exchange book after network and clock delay; quotes without fills remain unevaluable. Do not infer price impact or counterfactual execution in other conditions.
