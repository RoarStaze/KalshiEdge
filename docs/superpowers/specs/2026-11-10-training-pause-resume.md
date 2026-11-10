# Pause/resume and restart-safe training design (2026-11-10)

## Goal
Offer **Pause** / **Resume** in Train; never lose the completed verified historical-market dataset, completed fitted candidate, or irreversible evaluation decision on app shutdown. On application reopening automatically resume training that was active when the program closed; a user-paused run stays paused.

## Existing engineering constraints
- Train subprocess owned by history server; Windows launcher force-kills the server's child tree on shutdown.
- Dataset builder writes authenticated per-market JSONL checkpoints, each with input hashes, and repairs an interrupted trailing append on restart; a rebuild can reuse checkpoints safely.
- sklearn model fitting is not an incremental serializable computation. It cannot truthfully resume halfway through a single fit. Stage-boundary cooperative pauses are supported; interruption mid-fit requires repeating **only the unfinished fit stage**.
- Fitted experiment artifacts are already saved and sealed by `train_experiment`; evaluation artifacts are one-time and never replayed after recorded settlement evaluation.

## Proposed semantics
- One durable control request file `training_runs/control.json` with `run_id`, `pause_requested` and `manual_pause` values. Persisted run state `training_runs/current.json` stores mode, exact Git SHA, run ID, stage and completed progress. Requests for earlier runs never affect new sessions.
- `POST /api/training/pause` with the existing same-origin/CSRF guard requests pause. Worker reaches a safe checkpoint, writes **PAUSED** and exits. On a dataset stage this is after at most the current market; on fitting/evaluation after the current atomic stage. UI says **Pause requested** until actually paused, not falsely instantaneous.
- `POST /api/training/resume` launches a new worker with the *same* validated run identity and Git SHA; input fingerprints / artifact hashes revalidated. Existing dataset checkpoints are reused rather than rebuilt from zero; if the complete fitted candidate artifact was saved, evaluation resumes from that bundle. Do not redo an evaluated experiment / promotion.
- On dashboard startup, if last run is active or auto-paused from shutdown, and not manually paused, launch same worker automatically. If the Git SHA or verified input set changed, **fail closed** and show an actionable reason; never start an incompatible saved experiment.
- On ordinary dashboard shutdown, request an auto-pause and allow worker to reach safe boundary; if Windows force-kills mid-stage, the next run replays only the unfinished stage. Manual pause intent persists through restart. Correct final outcomes and model numbering unaffected.
- API progress reports `running`, `pause_requested`, `paused`, `can_resume`, `auto_resume` and resumes from previous checkpoint count; current UI gains two real buttons with accessible statuses.
- Never touch exchange orders, AutoTrade risk gates, live prediction, or model promotion rules.

## Safety
- No persistent Python memory serialization or unsafe pickle loading. Reuse only signed/provenance-checked dataset rows or authenticated model bundles.
- No duplicate overlapping workers; keep process-held exclusive worker lock and prevent duplicate user clicks.
- Skip promotion replay if an experiment already has a persisted one-time evaluation report. Fail closed and require manual review for ambiguous output.
- The deadline between requesting pause and actual paused state depends on the currently executing atomic model stage.
