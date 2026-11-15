# Historical 60% vs 69% confidence comparison: strict descriptive-only

## User request
Compare saved KalshiEdge Model 1 and rejected Model 2 on the same 1,222-market 480-second holdout at **60% confidence**, showing how qualified coverage and accuracy change versus 69%.

## Repository evidence and unavoidable constraint
The immutable evaluation report stores 69%-confidence gate counts and descriptive paired ticker identities but **not per-market probabilities**. Therefore the exact 60%-confidence counts cannot be computed from the saved report. It is impossible to claim a 60% outcome without retrieving probabilities again.

## Approach
Create a separate opt-in **read-only post-hoc diagnostic** that reconstructs the 480s predictions from the same hash-verified original dataset and verified model bundles, with explicit `--acknowledge-posthoc`. Do not write/replace any report, active model pointer, training state, or training code. Do not treat this as a fresh lockbox or use it to change an automatic-promotion decision. Previously tested historical data are **already exposed** and post-hoc threshold selection risks overfitting.

## Integrity gates
- Require most recent completed **baseline** retraining state with report matching exact saved experiment SHA.
- Verify experiment and active/previous Model1 bundle SHA and model/version schema match. Do not assume the currently active model is Model 1; match numbered active/previous pointer.
- Hash-verify the original Parquet source before any label read and require it matches the original candidate dataset SHA.
- Read **only the original already-consumed lockbox interval**; preserve one causal 478–480s checkpoint per distinct ticker using the original selection helper.
- Require the identical saved market count and exactly reproduce the original candidate/incumbent 69% gate counts before printing the requested 60% result. This catches changed model behavior or swapped model identity.
- Print actual qualified/correct/skipped/coverage/qualified-accuracy for both models, change from 69%, all-market accuracy, and matched-market overlap; never predict profit without quotes and fees.
- Explicit `--acknowledge-posthoc`, no write operations, trading, AutoTrade, state mutation, thresholds or gate changes; experiment preview only.

## Safety and limitations
Historical 60% result is exploratory because the same holdout already influenced model-selection discussion. Rerunning the consumed lockbox for descriptive what-if is not permitted as a new independent evaluation and does not replace the separately sealed one-time evaluation report.
If original feature dataset changed or artifacts unavailable, fail with clear explanation; do not silently compare different markets.
GPU/CPU scoring can be time-intensive; results require execution on the user's local laptop, whose archive isn't in GitHub CI.
