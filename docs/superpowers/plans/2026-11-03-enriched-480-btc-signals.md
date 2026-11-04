# Four extra causal BTC features — one fixed 480-second challenger (Nov 3, 2026)

## Root-cause evidence

Two same-six-feature research models almost always agree when both cross the
69% directional-confidence gate. Adding 242 nonlinear-only directions in the
5,190-market screen cohort yielded 203 right / 39 wrong (83.88%); adding 239
nonlinear-only in the 5,191-market confirm yielded 205 right / 34 wrong (85.77%).
Both are below original six-feature logistic conditional accuracy
(93.13% and 91.61%, respectively). The naive union's conditional accuracy
therefore fell. Changing architecture alone did not solve the joint goal.

Review of `bootstrap/features.py` confirms additional at-checkpoint BTC
signals **already built** but excluded from both six-feature models:
15-second BTC return, 60-second Binance taker-buy volume imbalance, and
high/low BTC excursions from strike since market open. Each derives only
from BTC bars completed no later than the checkpoint.

## One fixed new candidate (no grid or threshold tuning)

- Same verified historical pre-lockbox development/calibration partition.
- Same strict 480s, unique ticker, healthy two-sided Kalshi quotes and
  source-timestamp checking as the previous study.
- Four **predeclared additional features**:
  `btc_return_15s`, `btc_taker_buy_imbalance_60s`,
  `btc_market_high_excursion_bps`, `btc_market_low_excursion_bps`.
- Require `btc_return_15s_available>=1`, all four finite, imbalance
  within [-1,1], and low excursion <= high excursion.
  Exclude records missing/invalid; do not fill from future or settlement.
- Train three models on **identical** eligible earliest 60% development
  rows: fixed existing standardized six-feature logistic (C=.15), fixed
  existing six-feature HistGradientBoostingClassifier, and the **same
  fixed tree settings** on ten features. No parameter or feature search.
- Evaluate each on later 20% screen and final 20% confirm development
  blocks. Same 69% directional gate. One complete 15-minute block
  separation, no market/group overlap. Report Brier, log loss, ECE,
  issued accuracy, coverage, correct-per-market, all errors.
- Joint objective: enriched nonlinear ten-feature model must strictly
  exceed both six-feature comparators in conditional accuracy, coverage,
  and correct-per-market in BOTH development periods (>=100
  candidate-qualified markets in each). Otherwise fail closed.
- Only if both blocks pass, describe results on later historical 2,900-case
  prelockbox calibration (already repeatedly inspected, NOT independent).
- No model weights saved, no threshold changes, no live V5/frozen model
  or AutoTrade edits; never touch reserved lockbox.

## Implementation

1. Test-first `tests/bootstrap/test_enriched_direction_480.py` (RED
   confirmed on CI when missing module) covering fixed feature contract,
   causal eligibility, duplicate/date exclusions, chronology, paired counts,
   calibration label independence, no mutation.
2. `src/kalshi_edge/bootstrap/enriched_direction_480.py` pure evaluator
   plus research-only CLI on the verified existing local feature parquet.
3. `docs/ENRICHED_480_BTC_FEATURES_2026-11-03.md`: operator command,
   strict interpretation, flags and limitations.
4. Current-head CI full tests + container build green, diff reviewed,
   then and only then merge PR.

## Warning

The repeatedly inspected historical development periods are **not** a
fresh future test. Even a positive retrospective result must be frozen
and evaluated prospectively on future timestamp-aligned official
settlements, with after-fees executable quote outcomes separately
verified. This work cannot establish any profitable live advantage.
