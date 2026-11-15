# Coverage-first KalshiEdge live 63% and future model promotion policy

**Date:** 2026-11-14

## Explicit user decision
User requests Model #2 active at 63% minimum confidence in live 8-minute final prediction and AutoTrade. Refined primary goal: reduce the count of SKIPs and promote future models with materially higher coverage even if qualified accuracy is modestly lower; do not require accuracy improvements every retrain.

## Verified evidence and constraint
On 1,222 archived 480s markets, Model1 at 63% issued 489 forecasts, 440 correct, 49 incorrect (89.98%); Model2 at 63% issued 502 forecasts, 450 correct, 52 incorrect (89.64%). Model2 has 13 fewer skips and 10 more correct forecasts at cost of 3 additional incorrect. This was post-hoc on inspected history; it does not establish future accuracy or profitability. `models/default.json` is on the user's Windows machine, not GitHub. Merging cannot activate it.

## Feature 1: Live policy
- Change minimum confidence to 0.63 at the immutable 480s final forecast gate and in AutoTrade's additional *independent* confidence validation. Keep coin-flip veto, no stale or missing feeds, single lock per market, preflight price/liquidity/economic gates, no orders before locked final.
- Use a shared constant `MINIMUM_FINAL_CONFIDENCE` imported in both paths and dashboard metadata. Update UI explanatory text and skip reason to `CONFIDENCE_BELOW_63_PERCENT` for **new** records only. Preserve legacy records for history display and parsing.
- Clearly distinguish model confidence from measured accuracy. Holdout research 69%-gate report remains unchanged.

## Feature 2: Future coverage-first full retrain promotion
- Do not retroactively change sealed original v2 evaluations. New rule ID v3; preserve historical v2 verification and allow old promoted model to load.
- On **new, fresh, unseen** full retrain 480s lockbox only, require identical cohort of >=80 markets with both models evaluated at the 0.63 locked confidence gate, candidate strictly increases qualified forecasts over incumbent.
- Qualified directional accuracy may decline by **at most 0.5 percentage points**, relative to incumbent measured on same cohort. All-market 480s directional accuracy may decline by **at most 1.0 percentage point**. These are conservative policy assumptions for \"slightly less\" and can be revised prospectively, not after seeing the sample.
- Permit incumbent Brier deterioration no more than 0.005 and log-loss deterioration no more than 0.02 on full-sample and 480s checkpoint, and ECE degradation no more than existing +0.02. Require Kalshi benchmark superiority, source integrity, holdout independence, ablations, minimum sample, and existing coinflip safety.
- Do not require qualified accuracy or overall accuracy to increase for promotion once coverage improves, provided bounded degradations are met.
- Preserve previous volatility challenger policy for separate research paths; do not silently change its 69% tests without evidence.
- Existing v2 report's v2 decision must remain reproducible for the saved manually promoted Model2 candidate. Local manual activation uses saved exact SHA and active model #1 pointer, with explicit risk acceptance. Never auto-activate a model by merging to GitHub.

## Verification and operational separation
- RED then GREEN tests for exact 63% live gate/AutoTrade parity including .629 SKIP and .63 qualifying, missing/sick feed and coinflip veto unchanged, dashboard display.
- Coverage-first v3 gate tests: slight accuracy/probability decline accepted when coverage improves; rejected if no coverage gain, large accuracy or probability degradation, report integrity failure, wrong gate, missing markets; unchanged historic v2 decision and old model live loader/manual activation.
- No trades, credentials or local processes touched by GitHub changes. User must update stopped installation, confirm saved candidate identity, explicitly activate exact Model2 SHA, restart and verify both SHA and 63% configuration. AutoTrade stays off until user arms.
