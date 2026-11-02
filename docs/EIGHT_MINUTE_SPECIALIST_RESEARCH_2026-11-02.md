# BTC15m eight-minute specialist: causal feature-set research

The user requested **fewer confidence SKIPs AND higher directional accuracy**. The two earlier re-score challengers on previously inspected archived eight-minute predictions failed. This experiment addresses a different limitation by training a **new 480-second-specific model** on the historical feature dataset, not just rescaling the existing AI's probability.

## Run in a second terminal, no restart needed

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

$r = & $py -m kalshi_edge.bootstrap.specialist_480 | ConvertFrom-Json
$r | Select-Object status, fit_markets, validation_markets,
    brier_candidate_minus_market, log_loss_candidate_minus_market,
    verified_feature_dataset_sha256 | Format-List
$r.candidate | Select-Object markets, accuracy, brier, log_loss, ece, sharpness | Format-List
$r.kalshi_midpoint | Select-Object markets, accuracy, brier, log_loss, ece | Format-List
$r.candidate_69_gate | Format-List
```

If `status` is `DATASET_UNAVAILABLE_OR_UNVERIFIED`, inspect its `error` field. This code **does not** download raw history, rebuild the dataset, or weaken manifest/source checks. A missing or unverified dataset cannot be used to support a confidence increase.

## Data and training

- Load `data/bootstrap/derived/features.parquet` only after its signed manifest and provenance hashes pass the existing training verification. Its per-market chronological split is read from the existing training partition plan.
- Read **development and calibration groups before** the plan's reserved lockbox; do not load lockbox outcomes or evaluate the final held-out period.
- Keep only recorded **480±1 second** checkpoint rows with one unique market ticker, healthy finite features, present positive BTC 60s and 300s volatility, 60s return availability, and consistent two-sided Kalshi bid/ask.
- Reject future-dated source timestamps, Binance bars more than 5 seconds old, candle sources more than 120 seconds old, crossed/missing quotes or malformed market prices. No later quote filling or interpolation.
- Fit a fixed `StandardScaler + LogisticRegression(C=0.15)` on at least 80 earlier development markets; do not search alternate hyperparameters after examining results.
- Evaluate on at least 20 different chronologically later calibration markets after a full-round embargo. Require both outcome classes in training.

The **six** prespecified inputs are normalized BTC distance from the strike, realized volatility (60s, 300s), BTC 60s momentum, Kalshi quoted YES midpoint, and Kalshi spread. These are already available in the verified feature dataset, so the new experiment requires no API credentials.

## Reported objectives

The analysis reports candidate and historical Kalshi midpoint Brier score, log loss, ECE, overall directional accuracy, and the number and success rate of forecasts whose **unchanged** confidence transformation `2*abs(P(YES)-0.5)` clears the 69% cutoff.

The Kalshi midpoint is calculated from the *last fully completed recorded candle*, up to 120 seconds old, and is **not an executable bid/ask quote at the precise 480s instant**. The comparison is useful as a reference, not a profitability claim. The `candidate_69_gate` and `kalshi_69_gate` are hypothetical forecast cohorts, not actual order fills or trade profitability.

**Critical limitation:** the calibration group is out-of-fit for this candidate but is **not a fresh independent lockbox**. Existing models and prior human research may have used those historical markets. This command does not compare against an independently scored incumbent on the same sealed period and therefore cannot establish a deployed improvement or authorize promotion.

## Deployment gate (not performed here)

A real replacement must first freeze its artifact and exact training inputs, then evaluate once against the current promoted model and Kalshi on **previously untouched, future 480-second markets**. Confirm (1) better proper scores, (2) nondecreasing overall directional accuracy, (3) higher qualifying coverage **without** reducing the qualifying hit rate, (4) robust uncertainty intervals across calendar/volatility regimes, and (5) positive fee/quote/fill-aware executed net value. If any criterion fails, keep the production model.

**No model artifact is saved, no API calls are made, no live confidence threshold changes, no AutoTrade orders or account modifications.**

## References
- Kalshi, crypto benchmark and contract-specific settlement: https://help.kalshi.com/en/articles/13823838-crypto-markets
- scikit-learn, temporal evaluation: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- scikit-learn, probability calibration and proper scoring: https://scikit-learn.org/stable/modules/calibration.html
