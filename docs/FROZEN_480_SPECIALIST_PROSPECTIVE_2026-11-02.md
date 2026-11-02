# Frozen eight-minute specialist — prepare for untouched future shadow evaluation

The 2,900 historical calibration markets were **already inspected**. The
specialist produced 949 qualifying forecasts (863 correct), versus 738
qualifying Kalshi reference forecasts (672 correct); qualifying accuracies
were 90.94% and 91.06%. On matched observations there were 309
specialist-only qualifying markets (269 correct) and 98 Kalshi-only
markets (78 correct), a net +211 qualifiers and +191 correct qualifiers.

**No confirmed joint accuracy/coverage win** exists under the strict
selected-accuracy condition, nor is there independent proof of superiority to
the currently promoted model.

## Freeze a stable candidate for *future* evidence

Run after updating your repository:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.frozen_specialist_480
```

The command validates the historical feature dataset's manifest and
provenance, reads the prelockbox development partition and trains one
**unchanged**, regularized `StandardScaler + LogisticRegression(C=.15)`
with the six prespecified features. It never fits on calibration outcomes
or reserved lockbox outcomes. It exports only numeric coefficients,
means/scales, fixed schema, training provenance, freeze timestamp, and a
canonical SHA-256 integrity hash. No `pickle` or executable model payload is
loaded from this artifact.

The frozen file is exclusively created at
`data/bootstrap/research/480_specialist/frozen.json`. If an artifact is
already there, the command only verifies it and reports
`EXISTING_FROZEN_MODEL`; it does **not** retrain, move the freeze time, or
overwrite the model. Tampering or invalid provenance fails closed.
The hash detects accidental edits but is *not* a third-party signature.

The offline `score_frozen_specialist` helper can score only individually
verified **future** observations timestamped strictly after the freeze time,
recorded at the genuine 480±1-second checkpoint with the required original
Kalshi/Binance source timestamps and healthy historical candle inputs.
A historical calibration observation can never be scored as a fresh
prospective observation.

## Status and next integration

**Foundation only:** Freezing a model does **not** connect its scoring to
the currently running live data feeds. A separate implementation must
record one immutable paired specialist/incumbent probability per **future**
eight-minute market, including absent/invalid values as skips, before
settlement, and score official outcomes only after settlement. The
comparison must enforce model hashes, same timestamp and ticker, and day-
clustered uncertainty; record fee/spread/fill economics separately.

The freezing command does **not** load an AutoTrade account, set a live
confidence, overwrite `models/default.json`, modify the UI, start a new
trading loop, or place/cancel orders. It will not increase live coverage
or accuracy by itself. The existing model remains active.

A joint promotion would require future same-market accuracy improvement,
increased 69%-qualified coverage, nondecreasing selected hit-rate,
probability proper-score/calibration improvement, plus realistic positive
after-fee execution value.
