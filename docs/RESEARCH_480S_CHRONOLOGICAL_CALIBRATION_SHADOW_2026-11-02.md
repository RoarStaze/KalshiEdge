# Eight-minute confidence: a fixed chronological shadow calibration experiment

**Research only.** This command changes no running model, AutoTrade
order, threshold, confidence display, bank balance, or production artifact.

## What triggered this

The local replay reported 99 actual archived 480-second predictions on
officially settled legacy markets. At the old 69% confidence gate,
34 / 99 would have qualified and 31 / 34 were directionally correct.
The 65 below-gate observations included 41 correct predictions and 24
incorrect predictions. That is a descriptive retrospective result,
not evidence that a lower gate or more extreme probabilities would make money.

The current, genuinely immutable eight-minute policy had only 9 settled
forecasts, of which 7 were low-confidence SKIPs. These 9 are NOT used
to fit this replay experiment.

## The hypothesis and method

Some probabilities might be underconfident *at 480 seconds*. Test one
prespecified transformation instead of arbitrarily increasing confidence:

1. Load the last 100 legacy settled markets with original, correctly
   timestamped one-second prediction archives. Only the earliest
   observation within the original checkpoint's two-second window is
   eligible; unhealthy/absent/ambiguous observations are excluded.
2. Keep one row per ticker, 480s checkpoint, official settlement, consistent
   p_yes/p_no/confidence, and a single identical model hash. Multiple
   promoted model hashes are **not** pooled.
3. Chronologically sort rows. Fit a fixed Platt sigmoid using
   **exactly the earliest 60 markets**. Require BOTH outcome classes.
4. Evaluate predictions on the next 20 or more markets **without
   refitting on evaluation labels**. A negative fitted calibration slope
   is reported as nonmonotone/invalid rather than blindly flipping sides.
5. Compare incumbent and candidate on identical evaluation markets:
   Brier, log loss, accuracy, ECE, sharpness, and hypothetical 69%-confidence
   gate coverage and wins.
6. When the original Kalshi checkpoint bid/ask exists, score its midpoint
   and both predictions on exactly the **same quoted subset**. The midpoint
   is not executable.
7. Never write a calibrator, promote, change live confidence, alter AutoTrade,
   or place any orders.

The 60/20 minimum and the logistic penalty C=1 are deliberately fixed
before evaluating. They are research design parameters, NOT proof
of statistical power or a valid independent lockbox. Adjacent BTC15
markets can be highly correlated, and this dataset has already been
inspected repeatedly. No model-selection decision is authorized here.

## Run

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.bootstrap.shadow_calibration_480
```

Use `--last-markets 100` to make the scope explicit. Larger window
may include different model hashes; the experiment refuses to
fit a pooled calibration in that case.

Read:
- `status`: only RETROSPECTIVE_SHADOW_ONLY means the shadow comparison ran.
  An insufficient sample, mixed hashes, single-class training set, or
  nonmonotone candidate means **do not interpret a result as improvement**.
- `incumbent`, `candidate`: proper scores on the **same later period**.
  A positive `brier_improvement` means the candidate had lower Brier;
  positive `log_loss_improvement` means lower log loss.
- `incumbent_69_gate`, `candidate_69_gate`: purely counterfactual
  above-threshold forecast counts and accuracy, NOT actual entries or fills.
- `market_midpoint`, `incumbent_matched_market`,
  `candidate_matched_market`: strict same-quote subset.
- `model_promotion: false`, `live_trades: false`,
  `independent_validation: false`: always.

If the candidate wins retrospectively, it still MUST be frozen and
tested prospectively on untouched future 480-second markets, compared
against incumbent and Kalshi and executed-price/fee P/L, before anyone
could justify deploying it. If the candidate loses, reject it and
avoid boosting the UI confidence. Do not select a more flattering
number of training markets after seeing these results.

## Evidence

- scikit-learn, [Probability calibration](https://scikit-learn.org/stable/modules/calibration):
  fit a calibrator on information independent of model fitting; isotonic
  calibration is particularly overfit-prone on small datasets.
- Gneiting et al. (2007), *Probabilistic forecasts, calibration and
  sharpness*: https://academic.oup.com/jrsssb/article/69/2/243/7109375
