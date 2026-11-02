# Eight-minute multi-signal challenger — prospective evidence still required

**Purpose:** Test whether combining more causal data produces *both* better probabilities and more justified high-confidence forecasts than the incumbent. No live AI or trading modification is made.

## User-observed baseline

The earlier 480s archive had 99 historical settled checkpoint observations. At the current 69% confidence gate, 34/99 qualified and 31/34 were correct. On the later 39-market evaluation of the previous Platt calibrator, the incumbent produced 10/39 qualifying signals and 9/10 correct, while the sigmoid calibrator produced 6/39 and 6/6 correct. The sigmoid candidate was not promoted because Brier and log loss both worsened. We should not simply increase the displayed confidence.

## New, frozen challenger

A small regularized logistic model receives three **point-in-time** predictors from the *first actual* archived eight-minute checkpoint tick:

1. Incumbent model probability P(YES) in log-odds.
2. Contemporaneous Kalshi YES bid/ask midpoint probability in log-odds.
3. The signed BRTI price relative to the market's own strike, normalized in 100-basis-point units and clipped to ±5.

All sources must belong to that same observed forecast tick. BRTI value and positive strike are mandatory; BRTI source timestamp may be no later than the snapshot and no more than 5 seconds stale. Both Kalshi quote sides must be valid with bid ≤ ask. Invalid/missing inputs are excluded, NOT imputed or substituted with future prices.

The original model bundle hash must be identical throughout the selected sample. Duplicate market tickers exclude every duplicate. The existing eight-minute replay independently rejects unhealthy/late/ambiguous first observations and only attaches a market's official outcome **for research scoring after its settlement**.

A fixed first 60 chronological eligible markets train `StandardScaler + LogisticRegression(C=0.15, solver=lbfgs)`, and a later at least 20 markets are the evaluation cohort. The scaler and model are fit only on the training set; no future labels, gate optimization or hyperparameter search are allowed.

## Required results to meet the research joint objective

The new report compares, on **identical later evaluation markets**:
- Incumbent and candidate overall direction accuracy.
- Brier score and log loss (both must **decrease**).
- 69% confidence coverage (must **increase**) and above-gate hit rate (must **increase**).
- Kalshi checkpoint midpoint proper scores on the same observations.

`joint_target_met_retrospectively` is true only if *all five* move in the desired direction, including both accuracy metrics and both proper scores. A comparison with no qualifying incumbent signals does not establish a qualifying-accuracy improvement.

**Even a positive retrospective flag is not sufficient to deploy.** The sample was repeatedly inspected in prior experiments; historical re-testing of new candidates is susceptible to selection bias and overfitting. New untouched future 480s settlements, multiple BTC volatility regimes, and fee/quote/depth-aware execution economics are needed before considering promotion. A successful model signal does not guarantee that Kalshi's quoted ask can be filled profitably.

## Run offline while KalshiEdge stays running

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.shadow_strike_challenger_480
```

The command reads local archived forecasts, first-tick prediction JSONL and official saved settlements. It does **not** create model bundles, change the training state, touch AutoTrade, place orders, query Kalshi, or modify the user's bankroll.

Read `status` first. `INSUFFICIENT_MARKETS`, `MIXED_MODEL_HASHES`, `INSUFFICIENT_TRAIN_LABEL_VARIATION` and `AMBIGUOUS_CHRONOLOGY` mean no interpretable candidate comparison. The available sample may be smaller than 99 if BRTI or Kalshi quote data was not archived in earlier software versions.

## References

- scikit-learn, TimeSeriesSplit and temporal validation: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- Kalshi order-book quote depth and spreads: https://help.kalshi.com/en/articles/13823828-the-orderbook
- Gneiting & Raftery (2007), Strictly Proper Scoring Rules: https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
