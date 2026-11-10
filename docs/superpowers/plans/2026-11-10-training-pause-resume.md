# Implementation plan — recoverable Train Pause/Resume

1. **Tests first:** unit tests for durable pause request, worker callback and market checkpoints, controller mode/run continuity, automatic resume, manual pause across restart, and authorized pause/resume API. Tests must fail meaningfully before implementation.
2. **Worker control:** `training_pause.py` handles validated control JSON and cooperative pause exceptions. Extend `training_cycle.py` stage transition helpers to write PAUSED only after safe checkpoint; keep same run ID and Git SHA across reentry.
3. **Resume artifacts:** Use existing dataset checkpoint in `dataset.py`. Record authenticated experiment path when fit finishes; re-enter evaluation from saved bundle only if lockbox not previously evaluated. Never claim mid-fit memory continuity.
4. **Controller and lifecycle:** Add pause/resume/recover with strict process serialization. On local app shutdown pause, not unconditionally kill, with a bounded grace period. Automatically recover only formerly active runs; preserve manual pause across restarts.
5. **UI and security:** add Pause, Resume controls to Train; show Pause Requested/Paused/Auto Resume statuses; same-origin POST; fast 750ms progress updates. Do not affect Trade page.
6. **Verify:** run bootstrap tests, complete pytest, production container build, request PR review, merge only on green CI.
