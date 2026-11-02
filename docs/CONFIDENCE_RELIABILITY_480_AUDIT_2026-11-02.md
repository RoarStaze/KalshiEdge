# Confidence reliability diagnostic — final forecasts

This report answers where the original KXBTC15M AI's **recorded confidence**
corresponded to a correct/incorrect outcome, separately from how many
forecasts it actually issued. This is not a model fit, gate optimizer,
AutoTrade decision, or model promotion.

## Run on Windows PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
$r = & $py -m kalshi_edge.bootstrap.confidence_reliability_480 | ConvertFrom-Json

"INPUT INTEGRITY"
$r | Select-Object status, loaded_valid_final_records, invalid_final_records | Format-List

foreach ($name in @("legacy_240", "early_480", "forward_480")) {
    $cohort = $r.cohorts.$name
    "COHORT: $name"
    $cohort | Select-Object settled_markets, pending_settlements, invalid_settlements, decision_coverage, invalid_probability_records | Format-List

    "ACTUAL ISSUED DIRECTIONAL FORECASTS"
    $cohort.issued | Select-Object markets, correct, incorrect, accuracy, wilson_95_lower | Format-List

    "COUNTERFACTUAL DIRECTION OF CONFIDENCE-BASED SKIPS (NOT REAL FORECASTS)"
    $cohort.skips_with_valid_probabilities |
        Select-Object markets, hypothetically_correct, incorrect, hypothetical_directional_accuracy, wilson_95_lower |
        Format-List

    "FIXED CONFIDENCE BANDS"
    $cohort.bands.PSObject.Properties | ForEach-Object {
        [pscustomobject]@{
            band = $_.Name
            issued = $_.Value.issued.markets
            issued_correct = $_.Value.issued.correct
            issued_accuracy = $_.Value.issued.accuracy
            skipped = $_.Value.skipped.markets
            skip_direction_correct = $_.Value.skipped.hypothetically_correct
            skip_direction_accuracy = $_.Value.skipped.hypothetical_directional_accuracy
            skip_wilson_lower_95 = $_.Value.skipped.wilson_95_lower
        }
    } | Format-Table -AutoSize
}
```

## Critical definitions and limitations

- **legacy_240:** Historical outcomes at **4 minutes remaining**. They cannot
  be combined with the 8-minute scorecard or used as a validated performance
  claim about a model acting at 8 minutes.
- **early_480:** Eight-minute snapshots at/before the registered rescue-policy
  cutoff of **2026-11-02 13:00 UTC**, including historical evidence already
  examined when the rescue approach was chosen.
- **forward_480:** Strictly later eight-minute forecast checkpoints, with
  independent subsequent official settlements. New observations will appear
  without refitting or changing the bins.
- **Issued accuracy:** Immutable actual ABOVE/BELOW decisions only.
  **Skipped-direction accuracy:** A retrospective comparison of the side with
  higher saved `p_yes` or `p_no` to settlement; it does not mean a forecast
  was issued or a trade could have been placed.
- **Wilson 95% bounds** express finite-sample uncertainty under an idealized
  independence assumption; serially dependent BTC rounds could make those
  intervals too narrow. A band with fewer than 30 cases is tagged small-sample.
- Report includes *no* automatic selection of gates or best bins, *no*
  promotion recommendation, and *no* Kalshi executable bid/ask or fee-adjusted
  expectancy analysis.
- Invalid timestamps, duplicate records, corrupt settlements and inconsistent
  probability/confidence evidence are never silently accepted as successes.

Do not lower the live 69% threshold based on retrospective fixed-bin hit rates.
If a reliability hypothesis emerges, register its exact rule **before** testing
new, independent eight-minute markets.
