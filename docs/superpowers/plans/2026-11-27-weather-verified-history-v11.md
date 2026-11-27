# Superpowers V11 plan — Weather History

1. Test-first: add `tests/weather/test_hourly_history.py` with fixed raw official index + Kalshi settled ticker cases for correct/incorrect/SKIP/pending, wrong ticker, tamper, first-write persistence, city filtering, real close time, pagination, and zero denominator. Confirm RED.
2. Add `src/kalshi_edge/weather/hourly_history.py`: canonical audit of legacy one-per-event frozen snapshots and matching official outcomes; immutable correct/incorrect ledger with SHA and source integrity; separate read-only report. Confirm GREEN.
3. Wire verdict sync under `poll_hourly_settlements` and add live/preview GET endpoints; no POST, preview no artificial performance. Add endpoint tests.
4. Implement `/weather/history` as Bitcoin History-style four summary cards + timestamp + filter + 7-column table; all text via textContent, allowlist status classes, 15-second refresh, accessible notices and paging. Keep original Train calibration intact.
5. Verify final-head GitHub CI pytest and production container build and inspect outcomes; merge into main on green and return local git pull + launch.

**Ruling:** Bitcoin's historical 69% gate is not Weather's policy. No retrospective thresholding; only literal 50/50 ties are SKIP. This avoids rewriting decisions from earlier model runs.
