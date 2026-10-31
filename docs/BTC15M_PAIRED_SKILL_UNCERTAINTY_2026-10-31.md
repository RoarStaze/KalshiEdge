# Paired AI-vs-market forecast uncertainty (research-only)

**Scope:** Prospective research tooling, without any live model or AutoTrade change.

## Why

A lower Brier score is not enough to claim higher predictive skill. The
difference between a model's score and contemporaneous Kalshi midpoint may
reflect sampling noise, especially when many 15-minute contracts occur within
one correlated BTC price regime.

The current read-only `python -m kalshi_edge.bootstrap.forward_monitor`
already scores the AI and Kalshi YES bid/ask midpoint on *the exact same*
settled markets where an immutable two-sided checkpoint quote was recorded.

This extension attaches `paired_uncertainty`:

- Calculate each market's paired score improvement:
  `(midpoint - outcome)^2 - (model - outcome)^2` for Brier,
  and market log loss minus model log loss.
- Group these paired differences by the *UTC settlement date*.
- Sample whole trading days with replacement 500 times, with a reproducible
  seed, retaining within-day correlation in each draw.
- Return descriptive 95% percentile intervals for Brier and log-loss
  improvement **only if** at least 50 matched markets from at least 7 UTC days
  are available (minimums are research heuristics, not a significance guarantee).
- Keep model-version breakouts. Missing/invalid bid-ask checkpoints remain
  excluded, and neither a model nor a stake can be promoted from the result.

## Reading the report

A positive value means the AI had a lower proper-score loss than the
Kalshi midpoint *on the matched observed sample*. A positive interval
suggests the same direction under this day-cluster resampling assumption.
A confidence interval crossing zero means the retrospective observations
are consistent with either method being better.

**This is not formal independence proof, causal evidence, market-outperformance
certification, or evidence of executable trading profitability.** Days can be
dependent, the sample is already inspected, midpoint quotes are not executable
asks, and real fees and fills are not in this comparison.

A valid live model-promotion decision needs a frozen method, a never-reused
future test period, market-level causal feature timestamps, a proper-score
benchmark, and net-execution profit comparison where executions are actually
possible. Avoid choosing thresholds after viewing the same lockbox.

## Sources

- Original calibration and proper-scoring theory:
  https://academic.oup.com/jrsssb/article/69/2/243/7109375
- Sequential forecast comparison under dependence:
  https://academic.oup.com/biomet/article/109/3/647/6375942
- Best practices and pitfalls in forecasting evaluation:
  https://link.springer.com/article/10.1007/s10618-022-00894-5
