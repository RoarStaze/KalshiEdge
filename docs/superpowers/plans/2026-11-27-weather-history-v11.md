# Weather History V11 task plan — 2026-11-27

Superpowers + Dyslex.ai: directly execute user-approved Bitcoin History design without unnecessary design questions.

1. Tests first: `tests/weather/test_hourly_history.py` for historical data integrity, duplicate checkpoint exclusion, true accuracy, pending outcomes and fail-closed results; UI/HTTP routes and JS syntax.
2. Confirm RED on GitHub Actions against tests-only branch.
3. New `src/kalshi_edge/weather/hourly_history.py` pure read-only source-audited SQLite view. Dedup selection independent of outcomes, V9 replay with source validators, legacy reconciliation with stored source checks and frozen selected_market. No writes or network.
4. In `src/kalshi_edge/weather/hourly_ui.py`, implement History-only four KPIs + wide table and filter, matching Bitcoin screenshot styling; leave Trade and Train untouched. Wire JS polling to standalone /api/weather/hourly/history endpoint and preserve /api/weather/hourly/calibration on Train.
5. Route in `src/kalshi_edge/bootstrap/final_history.py` and `src/kalshi_edge/bootstrap/preview_site.py`, preview returns zero values without creating DB.
6. Run final GitHub CI full pytest + production Docker; review one-event-one-count, accuracy denominators, timestamp truth, security, then squash merge to main and give PowerShell update command.

Ruling: weather model does not persist SKIP decisions or independent directional confidence. Show zero (not recorded) and — respectively rather than fabricate counts.
