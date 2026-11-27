# WeatherEdge V10 implementation

1. Add TDD HTML contract tests (`tests/weather/test_hourly_trade_layout.py`): accessible twin panels, slot ids, safe trade-disabled state, correct weather index labeling, reset of missing data, preserved calibration navigation; watch initial missing-design failures on CI.
2. Create `src/kalshi_edge/weather/hourly_trade_layout.py`: isolated main markup and CSS matching two-column Bitcoin Trade. Use real city select, no invented account, trade-side pills are read-only paper estimate selectors, disabled order button. Include hidden/expandable diagnostics IDs needed by existing JS, keep market ladder.
3. Modify `src/kalshi_edge/weather/hourly_ui.py`: use new layout only for forecast, compact forecast header, preserve all current forecast/status/calibration polling, wire model probabilities, quote freshness, countdown and paper calculator into new ids. Keep no-order state and have missing data fail closed. Update local side-flip watcher without stored history. Check JS with Node.
4. Full CI (pytest and Docker build) on final exact SHA. Only merge into main if all tests pass; deliver user's PowerShell git pull/start commands.

Ruling: user approved visual mock, but the illustrated cash balance, buy actions, 8-minute record and risk grade are demonstrative. Replace those with live evidence or clearly disabled research-only equivalents, preserving UI hierarchy without representing unavailable capability.
