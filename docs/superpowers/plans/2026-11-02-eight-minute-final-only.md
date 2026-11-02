# Eight-minute final forecast and final-only AutoTrade plan

> For agentic workers: use Superpowers TDD, verification-before-completion, and Dyslex.ai presentation support.

**Goal:** Lock the sole final BTC-15m forecast at 8 minutes remaining (480 s), and make this immutable ABOVE/BELOW decision the only AutoTrade BUY trigger. Remove all early-confidence BUY and reversal SELL trading branches without weakening order safety.

## Constraints
- Preserve existing minimum final confidence 69% and fail-closed SKIPs; no healthy direction should be fabricated from failed feeds. Preserve the separate fee-aware expected-value BUY veto, position/quote checks, and durable order journal. The new policy does **not** guarantee a trade each market.
- Disallow any new trade initiated on live continuous prediction before final lock.
- No post-trade automatic reversal; existing open holdings are never forcibly liquidated by this upgrade.
- Preserve continuous arming across future rounds, at-most-one-filled-BUY/market, optional existing bounded confirmed-zero-fill retry, and UNKNOWN-stop.
- Maintain historical 240-second forecast files in history and settlement with explicit new `checkpoint_seconds` field on future records (legacy defaults 240).
- Change dashboard checkpoint to 480s; only match final records with their own sealed checkpoint duration. Display clear 8-minute wording in UI.
- Avoid changing Kalshi historical contract resolution, confidence scoring, or retraining weights.
- Local running service must be stopped and restarted when user chooses to load new source. A running old service may continue trading on old logic until restart; avoid mixed-version behavior.

## Tasks
1. Commit failing integration tests covering new timing, immutable metadata, zero early trade, one final BUY, wrong old checkpoint ignored, and source policy.
2. Verify CI red. Change final model gate constant and record schema; update history/dash readers.
3. Refactor AutoTrade tick to wait exclusively for sealed 480s final and 69% final confidence; retain existing post-preview value gate and fresh quote, bounded post-checkpoint time.
4. Replace obsolete early and reversal tests with final-only risk tests; update clock constants in other regression suites; keep historical 240s tests as legacy fixtures.
5. Update UI labels and read-only diagnostic reasons. Write breaking-change migration guidance and restart commands.
6. Run full CI, verify production container built and merge to main only after success.

## Validation

Key acceptance: a confidence=1.0 live stream before 480s lock MUST cause 0 order previews and 0 orders. An 8-minute locked ABOVE or BELOW is the **only** entry signal, with one durable BUY journal and no reversal. Old 240s historical JSON still scores at correct market close. No historical backfills and no live trades during tests.

**Risks:** Prediction skill at 8m may differ materially from 4m; no improved expected profitability can be claimed. Existing fee/edge veto and SKIP behavior still determine number of fills; diagnose trade scarcity with audit instead of assuming switching checkpoint alone ensures more orders.
