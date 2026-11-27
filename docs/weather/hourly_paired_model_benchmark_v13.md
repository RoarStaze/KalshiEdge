# WeatherEdge V13 — paired temperature and market-probability evaluation

## Verified source constraints
- The repository contains model code and synthetic tests but **not the user's locally collected weatheredge.sqlite3 database**. GitHub CI alone cannot establish superiority on the user's real forecasts.
- Existing V12 chooser tests static trends, adaptive regression, volatility regimes, and previous-day analogues with a last-24-hour validation period.
- History selects Kalshi threshold markets near 50/50. Direction accuracy is not a substitute for temperature MAE or proper probabilistic scores.
- Weather trading remains paper-only; BTC/Gold untouched.

## Design
1. Fit a bounded regularized Huber hybrid on fast/slow hourly Index slopes, previous-day same-clock change and bounded drift. Sigma selection uses training CRPS only. No feature at or after the target time can influence the prediction.
2. Compare against the original V12 chooser in THREE consecutive, nonoverlapping 24-hour evaluation blocks. Every block trains on the earlier prefix, then produces paired predictions for the exact same 24 target hours. No data from later blocks may influence earlier models.
3. Report paired temperature MAE, probabilistic CRPS, 90% interval coverage, and differences. When the exact original frozen Kalshi market ticker/threshold and subsequently verified official finalized YES/NO outcome are available, report paired Brier, log loss, directional accuracy; otherwise these fields are null.
4. Precommitted promotion criteria: three valid blocks/72 matched hours; MAE improves >=0.05 F, CRPS improves >=0.025, at least two block CRPS wins, 90% coverage >=75% and loss <=7 pp; additionally at least 24 verified exact market outcomes, Brier improvement >=0.01 and directional accuracy cannot decline by more than 1 pp. If evidence is insufficient, new model stays shadow-only.
5. Produce a read-only per-city/per-lead comparison using local persisted Index observations and separately verified market outcome evidence. No retrospective alteration of frozen forecasts, SKIP labels, or counts.
6. A candidate can become the live model only after these gates pass, with a unique model version invalidating prior physical ensemble lockbox validation. Otherwise use the current forecast without change.

## Evidence limits
A deterministic synthetic win demonstrates mechanics, not real superiority. A positive result using local past observations remains retrospective and is not equivalent to new prospective settlement calibration. The user can run the local comparison with one Python command while the normal app stays otherwise one-command-start.
