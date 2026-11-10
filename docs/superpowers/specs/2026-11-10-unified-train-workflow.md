# Unified Train Now and Visible Training Workflow — Design

## Goal
Replace the old two-button training UI with **one Train Now** action that actually starts an eligible training path and renders every real training and evaluation stage. Display a trustworthy final decision, its measured reasons, and whether the model was **actually activated** (not merely fitted or scored).

## Existing behavior and defect
- Train Now is restricted to an older historical mistake-cycle whose training cases are currently 0/12, although fresh historical feature groups are 472/20.
- A separate “Evaluate 480s challenger” synchronous HTTP POST can fit an offline model while Train Now stays disabled. Its returned metrics disappear on refresh and it cannot activate the model.
- The forward 480s cohort has >120 usable snapshots (303 in user's screenshot) but its current candidate achieved 15/15 qualified vs incumbent 40/43 on a matched 91-market cohort: **fails joint accuracy+coverage objective**.
- Legacy training can already promote **only** after independent gate checks through `run_lockbox_evaluation`, and a restart loads the hash-sealed promoted model. The forward six-feature specialist is NOT loadable as the current hash-sealed bootstrap pipeline: changing the live model by merely writing a “passed” flag would be unsafe and incorrect.

## UX contract
1. Single primary Train Now; remove extra Evaluate button. Precedence: legacy cycle if eligible and valid, otherwise eligible forward 480s training.
2. Readiness displays both evidence streams; the visible progress for *training* starts only when the worker runs, not from the number of historical markets.
3. Persistent stage timeline: validating evidence, preparing chronological splits, fitting, scoring untouched later validation, comparing with incumbent, promotion decision, completion. Stage percentages represent completed milestones, not percent of CPU training.
4. Live polling every 5s surfaces stage, last transition timestamp, worker PID/log info if available, and the candidate versus incumbent metrics. Progress and final report survive page refresh/restart. Worker interruption is explicit.
5. Terminal outcome:
   - **PROMOTED**: only when a compatible, independently evaluated, hash-sealed model is actually made active by the existing promotion pipeline.
   - **FAILED VALIDATION**: forward challenger that misses one or more gates, with concrete counts/accuracy/coverage/Brier reasons.
   - **NOT DEPLOYABLE**: forward candidate with favorable *retrospective* score but no independently confirmed, compatible live deployment mechanism. Explain missing prospective evidence rather than claiming PASS.
   - **ERROR**: invalid data, worker failure, or missing evidence. Never silently claim success.

## Safety
- Keep current active predictor and AutoTrade unchanged for forward research. No automatic deployment without a pre-registered prospective promotion gate and a compatible live inference artifact; old learning-cycle promotion remains active through its existing hash/provenance rules. This is an explicit limitation, not a fake pass.
- No user credentials or orders in training workers. Use existing single-worker lock, low OS priority, immutable input verification.
- Never re-classify an unverified settlement as a training example, manufacture observed progress/time estimates, or consider a tiny 100%-accuracy sample sufficient for live promotion.

## Files
- `training_cycle.py`: one selection/launch owner, robust worker state, forward worker stages and decision reasons.
- `forward_learning_480.py`: optional progress callback and robust explicit gate-failure reasons, no live promotion.
- `dashboard_ui.py`: one visible button, progress timeline, comparison, final outcome; no blocking synchronous evaluation.
- `final_history.py`: remove obsolete POST route to eliminate duplicate backdoors.
- `tests/bootstrap/`: button/routing, worker stages and terminal states, API protection, regression.
