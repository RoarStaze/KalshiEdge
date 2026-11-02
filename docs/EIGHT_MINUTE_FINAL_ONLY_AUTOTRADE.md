# KXBTC15M: 8-minute final forecast + final-only AutoTrade

**Implementation status:** Changes staged on PR #69, to merge only after successful CI.

## New behavior

- At **8 minutes (480 seconds) before market close**, the live predictor seals exactly one immutable final record: ABOVE, BELOW or SKIP. Earlier continuous predictions remain visible as *observations* but cannot submit AutoTrade orders.
- When explicitly armed, AutoTrade waits exclusively for that sealed final. Final ABOVE authorizes a BUY of UP; final BELOW authorizes a BUY of DOWN. Final SKIP or unhealthy/missing/late final authorizes no trade.
- **No early 90% confidence orders. No early position, reversal SELL, or opposite BUY** in the new live strategy. The application does not auto-sell any existing holdings from past versions.
- A final must pass the existing 69% confidence floor, a fresh quote, fee-aware estimated-value BUY gate, and explicit exchange/order checks. The program never guarantees a fill or favorable profitability. **The entry SIGNAL is one condition: the final record**, but existing trading *safety restrictions* still apply.
- The same requested dollar size is used per market in continuous mode, without increasing any stake. Confirmed zero-fill IOC may be retried at most once within the existing bounded final execution window; partial or filled BUYs are terminal for that market. UNKNOWN exchange outcome stops trading.
- The final timing and update take effect only after user performs a safe local stop, pull and restart.

## Backward compatibility and migration

Old final JSON files lock at 240 seconds; newer JSON files carry an explicit `checkpoint_seconds: 480` field. The model uses `checkpoint_seconds: 240` as the **legacy default only when loading old records**. This prevents the archived forecast-to-market settlement timestamp from drifting by 4 minutes and corrupting historical scoring.

Future dashboard final linkage requires a genuine 480-second checkpoint. A 240-second final record from an old process is not silently treated as the new live signal. Existing order-stage history, including legacy EARLY_BUY / REVERSAL_SELL fills, is retained for P/L reconciliation, though the new strategy does not submit such stages.

## Local update / restart

**IMPORTANT:** `git pull` alone does not change the code in an already running Python process. Stop AutoTrade safely and ensure no order is executing, then shut down the old `kalshiedge start` process. In PowerShell:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\kalshiedge.exe start
```

If the command is not present in the venv, activate the venv or use the existing `kalshiedge start` command once you verify it uses the correct installed repository.

Once restarted, inspect the live API or new forecasts for `checkpoint_seconds:480` and their original `scheduled_ts_ns` and `seconds_remaining` values. Old archives remain intact.

## Critical performance warning

A four-minute BTC15m model score is **not** evidence of the model's performance eight minutes before settlement. An earlier lock has less information about final resolution and may have substantially worse accuracy/calibration. This change is driven by the requested trading cadence, **not verified improved edge**.

One actual trade in 20 hours could have been caused by model SKIPs, restrictive fee-aware value checks, no quotes or fills, or persistent AutoTrade state. We have not inspected the authenticated local events and cannot identify the exact cause. Use `python -m kalshi_edge.trading.autotrade_execution_audit`, AutoTrade History, the read-only forecast and checkpoint monitors, and the Kalshi fill reconciliation to diagnose.

Do not infer a future profit improvement from test-only verification. No real orders are placed during PR CI.
