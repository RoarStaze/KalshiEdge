# Quote-side economics audit implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Steps use checkbox for test-first verification.

**Goal:** Read-only, research-only evaluation of whether immutable final predictions carried plausible economic value at the contemporaneous *side-specific best ask*, without confusing quoted prices with actual fills or paid fees.

**Architecture:** Reuse `read_final_history`, which already links immutable decision-time bid/ask to persisted official settlement. Add a standalone `quote_economics.py` module, entirely outside the trading and live-predictor processes, with a pure function and local CLI. No additional runtime capture or database changes.

**Tech Stack:** Python >=3.12, standard-library Decimal / JSON, existing Pydantic history, pytest.

**Spec:** Existing `forward_monitor` checks probability vs Kalshi midpoint, but midpoint is not purchasable. Kalshi bid/ask is available at the four-minute checkpoint for newer predictions. A hypothetical purchase of BELOW/NO pays `1 - YES bid`, whereas ABOVE/YES costs `YES ask`. This is a top-of-book quote *without depth, guaranteed fill, slippage or fees*. Kalshi fee schedules can vary.

## Scope and validation
- [ ] Commit failing tests for quote-side ask arithmetic, official settled hypothetical payout, low-confidence SKIP counterfactuals, missing quote and pending settlement exclusions, duplicate ticker fail-closed behavior, and optional explicitly hypothetical fee scenario.
- [ ] Confirm test-first CI fails because the new module is missing.
- [ ] Implement minimal pure audit, with deterministic strings and no exchange API calls.
- [ ] Run complete GitHub CI; fix failures; merge only once green.
- [ ] Keep `kalshiedge start` alive on Windows; CLI may run separately after local pull, without restarting the predictor.
- [ ] No live stake, order, threshold, or model changes.

## Review focus
- Missing price/prediction or crossed quote never becomes a phantom profitable trade.
- SKIP cases and pending official settlements excluded from executed-signal realized summaries.
- Same ticker duplicated across records excluded rather than double-counted.
- Fees only included when explicitly assumed, never represented as paid.
- 1 share per checkpoint is a hypothetical unit, NOT actual fill size or account P/L.

## Evidence limits
- The quote is a top-of-book observation, not a fill guarantee; liquidity/depth/latency can make the trade impossible.
- Assumed flat fee per contract is a user-defined hypothetical stress scenario; Kalshi's actual schedule depends on contract/volume/price and fee calculation.
- The model's historical probability may be miscalibrated; descriptive expected edge is not proof of future gain.
- The implementation is isolated from the AutoTrade order path.
