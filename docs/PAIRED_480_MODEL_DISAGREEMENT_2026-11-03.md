# Paired 480s logistic vs nonlinear disagreement diagnostic

## What this report measures

The nonlinear AI had lower Brier score and log loss on both previously
examined 480-second development periods, but the original logistic research
baseline had higher conditional accuracy when each applied the same 69%
directional confidence gate. The paired audit explains those differences
using exactly the same observations, without any new modeling.

**Both research models differ from the live V5 model.** Nothing here
proves a successful real-money trading rule.

## Run (PowerShell)

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.nonlinear_direction_480 | ConvertFrom-Json

"EXPERIMENT STATUS"
$r | Select-Object status, development_screen_markets, development_confirm_markets | Format-List

foreach ($name in @("development_screen", "development_confirm")) {
    $a = $r.$name.paired_disagreement
    if ($null -eq $a) { continue }

    "PAIRED COHORT: $name"
    $a | Select-Object markets, net_qualifying_change, net_correct_change, net_incorrect_change, ready_to_promote | Format-List

    "MUTUALLY EXCLUSIVE MARKET CATEGORIES"
    $a.categories.PSObject.Properties | ForEach-Object {
        [pscustomobject]@{
            Category = $_.Name
            Markets = $_.Value.markets
            LogisticRight = $_.Value.logistic_correct
            LogisticWrong = $_.Value.logistic_incorrect
            NonlinearRight = $_.Value.nonlinear_correct
            NonlinearWrong = $_.Value.nonlinear_incorrect
            DirectionAgrees = $_.Value.directions_agree
        }
    } | Format-Table -AutoSize

    "EXACT NET CHANGE EXPLANATION"
    $a.change_decomposition | Format-List

    "CURRENT LOGISTIC RESEARCH BASELINE"
    $a.baseline_logistic | Select-Object qualified, correct, incorrect, skipped, coverage, accuracy | Format-List

    "HYPOTHETICAL FIXED LOGISTIC-PREFERRED UNION"
    $a.logistic_preferred_union | Select-Object qualified, correct, incorrect, skipped, coverage, accuracy | Format-List
}
```

## Interpretation

- `both_qualified_agree`: both pass 69% gate and favor the same outcome.
  Because the directions agree, both are right or both wrong.
- `both_qualified_disagree`: both qualify but favor opposite outcomes.
  Exactly one is directionally correct, under a verified binary settlement.
- `logistic_only`: only logistic actually issues a forecast. The
  nonlinear preference is **not** an actual qualified forecast.
- `nonlinear_only`: only nonlinear issues a forecast. These are the
  potential additional predictions that **could** raise coverage, but
  errors are fully counted, and do not yet establish safety.
- `neither_qualified`: both SKIP. Neither is scored as an issued
  forecast.
- `change_decomposition`: net accuracy changes also include
  qualified forecasts *lost* by nonlinear and disagreements where both
  qualify. Therefore overall net correct changes are not simply the
  number of correct additional forecasts.
- `logistic_preferred_union`: one fixed hypothetical comparison that
  keeps logistic when it qualifies and uses nonlinear otherwise only
  if nonlinear qualifies. It does **not** optimize decisions, change
  deployed forecasts, or imply a profitable trade.

The audit is only descriptive, on already-inspected development data;
no untouched confirmation, new future prospective settlement cohort
or live decision improvement is established. The existing strict
DEVELOPMENT_REJECTED rule is preserved; historical 2,900 calibration
outcomes are NOT scored when either development block fails.
