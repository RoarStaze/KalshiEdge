# Live research shadow: frozen eight-minute specialist

This is **read-only instrumentation**. It does not change the incumbent model,
the final checkpoint decision, the 69%-confidence gate, UI, or AutoTrade.

## Prerequisite

The frozen JSON model must already exist at
`data/bootstrap/research/480_specialist/frozen.json`, created with:

```powershell
.\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.frozen_specialist_480
```

Once created, **do not delete or refit it** during this prospective evaluation.
The frozen model's timestamp and hash define the start of future evidence.

## Update and run

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
```

**Restart the existing KalshiEdge forecasting process** after updating so the
new Python code is loaded. The original live process prints
`READ-ONLY 480S SHADOW ENABLED: frozen_sha=...` on startup only if the file
passes hash integrity validation. If the file is missing or invalid, live
forecasting still works but research shadow collection is not enabled.

Each subsequent **new** immutable eight-minute final forecast is saved
first, then the optional, independently scored specialist shadow is written
at:

`data/bootstrap/research/480_specialist/live_shadow/YYYY-MM-DD/<ticker>.json`

The date is the market's UTC settlement-close date. Records include frozen
model SHA, incumbent model hash, scheduled and observed checkpoint timestamps,
incumbent P(YES), and shadow P(YES) or an explicit `SKIP` reason. A separate
SHA-256 check detects changes to saved data. A duplicate identical record is
accepted as already saved; a modified or corrupt one is rejected.

See evidence without altering anything:

```powershell
$root = ".\data\bootstrap\research\480_specialist\live_shadow"
Get-ChildItem $root -Filter '*.json' -Recurse -ErrorAction SilentlyContinue |
    ForEach-Object {
        Get-Content $_.FullName -Raw | ConvertFrom-Json
    } |
    Select-Object market_ticker,status,reason,p_yes,incumbent_p_yes,frozen_model_sha256 |
    Format-Table -AutoSize
```

**Do not use these preliminary outputs to place trades.** Future official
settlement scoring needs to include each market and every unavailable
prediction, compare the specialist and current promoted model on the same
market/time, and examine independent after-fee profitability. No such
prospective hit rate is established when the collector first starts.

### Failure isolation

If shadow features or model integrity cannot be established, the specialist
never manufactures a probability. A failed research writer is reported to
console but does not change the incumbent's already-persisted forecast or
any AutoTrade order decision. Collector restart is required for updates to
the frozen JSON, though mid-experiment model swapping is prohibited.
