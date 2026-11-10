# Train Now: end-to-end dataset → model → promotion

## Summary

One **Train Now** action controls training. There is no separate Evaluate Challenger button. Every fit has a durable run state, actual milestone timeline, immutable report, candidate/incumbent metrics when available, clear rejection reasons and an explicit deployed-model status.

### Pipeline

1. **Create dataset:** With a verified full-model training opportunity, build historical KXBTC15M features from Kalshi trades/candles and archived Binance input; if raw sources are unavailable, reuse the existing SHA-verified dataset and say so. A separate forward-only research mode creates a SHA-sealed snapshot of **causal 480-second observations, original predictions and official settlement labels**. No after-outcome reconstruction of market prices is allowed.
2. **Audit dataset:** Check signed source manifests, provenance hashes, feature/schema integrity, leaked future timestamps, official settlement classes and label validity. Abort on failed integrity or missing minimum samples. Dataset-building progress is the actual number of markets scanned, not a fake timer.
3. **Chronological split:** Use earlier data for development and disjoint later data for validation. The model's current production pipeline independently reserves a fresh lockbox and calibration section; forward-specialist research evaluates on later snapshots. Random train/test splits are not appropriate for time-ordered market labels.
4. **Fit candidate:** The full-compatible model uses the existing calibrated/stacked bootstrap pipeline, evaluates model-family ablations and produces a content-hash-sealed experiment. The six-feature forward model is a separate research candidate.
5. **Evaluate untouched lockbox:** Record probability Brier, log loss, calibration and directional accuracy. Reject a candidate if the required 480-second lockbox sample or market midpoint comparison is missing.
6. **Compare to current live model:** The same independent market IDs and checkpoint timestamps must be scored by candidate and incumbent. A full replacement must strictly improve conditional accuracy **and** prediction coverage at the same 69% confidence gate, without degrading required proper scores or leakage safeguards. Never turn SKIPs into imaginary winning trades.
7. **Promotion decision:** On a passing full-model comparison, the existing verified atomic promotion mechanism writes `models/default.json` with an incremented `model_number`, hash and previous hash. The already-running inference process continues using its **old** loaded bundle until the app restarts. A rejected candidate has no effect on the live default.

### Model # identification

`models/default.json` serves as the authoritative deployed model pointer. Older existing pointers implicitly begin at **Model #1**; every successful replacement gets the next integer. Failed trials, restarts and repeated status reads never increment that number. The Train UI compares the hash in the latest live prediction against the installed promoted hash; when different, it distinguishes **currently running model** from **newly installed, restart required**.

### Research-only fallback

If the legacy/historical full-model path is not eligible, Train Now can still prepare and fit the independently verified forward-480 research cohort. Its results are reported, but even a winning retrospective score is **NOT DEPLOYABLE**. The six-feature frozen research logistic model does not have the required serialized `FittedBootstrapPipeline` compatible live artifact, and using holdout labels to repeatedly select a candidate requires new untouched prospective confirmation. It is not safe to claim it replaced the production model just because its observed accuracy increased.

### Run and check on Windows

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
kalshiedge start
```

In **Train** check the model number, verified data requirements and one **Train Now** button. The training workflow advances the real milestones and displays a persistent final report. Training may use CPU, but runs in a separate lower-priority process to preserve the live predictor's responsiveness.

Programmatic status (read-only):
```powershell
Invoke-RestMethod http://127.0.0.1:8899/api/training |
  Select-Object current_model,selected_mode,train_available,run | Format-List
```

### Verification

GitHub Actions runs project bootstrap tests, all pytest checks, Python compilation and production container build. Tests cover data integrity, worker resume/interruption, single-button routing, historical baseline evaluation, 480s lockbox acceptance, strict accuracy+coverage promotion, version increments and model hash comparison. A successful test run validates the program wiring, **not** evidence that trading profitability has improved.

### Sources for evaluation policy

- [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html): ordered train/test with no future market leakage, optional gap.
- [scikit-learn Probability Calibration](https://scikit-learn.org/stable/modules/calibration.html): do not calibrate on in-sample predictions; use independent outcomes.

## Risk limitations

Live AutoTrade sizing, maker/taker quote economics, near-strike coin-flip risk handling, 69% forecast gate, 480-second immutable final and authenticated order routing are NOT modified by this training change. Any truly promoted model requires an application restart to load and does not guarantee greater future accuracy or profitability.
