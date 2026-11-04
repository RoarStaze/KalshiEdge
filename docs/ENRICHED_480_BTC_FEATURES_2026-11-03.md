# BTC 480s enriched-feature challenger (four fixed extra signals)

## Why

On 5,190 480-second development-screen markets, the original research
logistic qualified 1,702 markets at 93.13% hit rate. Adding 242 nonlinear-only
directions would yield 203 right and 39 wrong (83.88%). On the 5,191-market
development-confirm cohort, adding 239 yielded 205 right/34 wrong (85.77%).
The naive union therefore increased coverage at the expense of conditional
accuracy.

Review of `bootstrap/features.py` showed the historical derived dataset
already includes **more signal families than the six research model inputs**.
The following candidate is fixed *before examining its outcomes*.

## Precisely one fixed feature hypothesis

The predeclared four additional Binance-derived point-in-time features:
- `btc_return_15s`: price return over 15 seconds before checkpoint;
- `btc_taker_buy_imbalance_60s`: signed 60-second Binance taker
  buy/sell base-volume imbalance;
- `btc_market_high_excursion_bps`: BTC intramarket high relative to
  Kalshi strike, as known at checkpoint;
- `btc_market_low_excursion_bps`: BTC intramarket low relative to strike.

Require explicitly available 15-second history, finite values,
plausible signed imbalance [-1,1], and coherent low<=high. Every extra
signal derives from Binance bars completed no later than checkpoint;
source timestamp freshness and healthy quotes use the existing strict
480s eligibility checks. Invalid or absent extras exclude that market
**for all three comparators**, not just the enriched candidate.

Three models use exactly the same eligible training/evaluation markets:
1. Original six-feature standardized logistic (C=.15).
2. Fixed six-feature HGBT (140 iterations, .05 learning rate, 9 leaves,
   min leaf 80, L2 5).
3. **Same fixed HGBT architecture**, adding exactly the four features
   listed above, for ten total.

All trained once on the earliest 60% of verified development history.
Same 69% directional confidence gate; same later 20% development screen
and subsequent 20% development confirmation, with 15-minute boundaries.
No tuning and no refit between periods.

**Joint success** requires the ten-feature candidate to strictly
increase conditional hit rate, decision coverage, and correct forecasts
per market relative to *both* six-feature models in *both* subsequent
development cohorts, each with at least 100 candidate forecasts.
Otherwise `DEVELOPMENT_REJECTED`, with complete screen/confirm scores,
and no historical calibration scoring.

When both development blocks pass, the 2,900-market previously
examined historical calibration period is scored as **exploratory only**.
No fixed 480-second research result is the deployed V5 score.

## Run in PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.enriched_direction_480 | ConvertFrom-Json

"EXPERIMENT STATUS / ELIGIBILITY"
$r | Select-Object status, eligible_development_markets, eligible_historical_calibration_markets, fit_markets, screen_markets, confirm_markets | Format-List
$r.development_exclusions | Format-List

foreach ($name in @("screen", "confirm", "historical_calibration")) {
    $w = $r.$name
    if ($null -eq $w) { continue }
    "PERIOD: $name"
    $w | Select-Object markets, joint_vs_logistic, joint_vs_nonlinear_6, joint_objective | Format-List

    foreach ($model in @("logistic_6", "nonlinear_6", "nonlinear_enriched_10")) {
        "MODEL: $model"
        $w.$model.gate_69 | Select-Object qualified, correct, incorrect, skipped, accuracy, coverage, correct_per_market | Format-List
        $w.$model.probability_metrics | Select-Object brier, log_loss, ece | Format-List
    }
}
```

## Caveats

This is repeated **historical** modeling on a dataset inspected across
multiple earlier experiments. It has no untouched independent test and
cannot be used to justify deploying a live strategy, even if favorable.
It does not access reserved lockbox, save any weights, alter existing
forecast gates, or change AutoTrade. Directional accuracy is distinct
from execution, liquidity, fees and profit. A future prospectively
registered, immutable and time-matched official settlement scorecard
must precede any deployment recommendation.
