# Live 480-second coin-flip guard

The KXBTC15M **real** immutable final forecast now performs two checks at the
480-second-before-close checkpoint. The confidence gate remains at 0.69 (its
existing normalized model-strength definition). After an otherwise qualified
forecast, a causal BRTI near-strike veto can replace ABOVE/BELOW with
`SKIP` / `COIN_FLIP_RISK_HIGH`. No second forecast is produced.

## Veto policy

- Policy: `brti-near-strike-z0.20-v1`
- Compute \(z = |\ln(\mathrm{BRTI}/\mathrm{strike})| / (\sigma_{\mathrm{BRTI}}\sqrt{T_{\mathrm{effective}}})\).
- \(\sigma_{\mathrm{BRTI}}\) comes only from timestamped BRTI observations
  ending at the forecast checkpoint. The existing risk helper requires at least
  31 observations, 60 seconds of usable consecutive returns, and a 120-second
  trailing observation window. \(T_{\mathrm{effective}} = \max(1, T - 40\mathrm{s})\).
- `z <= 0.20`: `HIGH`; an otherwise eligible ABOVE or BELOW becomes `SKIP`.
- `z > 0.20`: `LOW`; existing direction and confidence rules remain in force.
- No valid risk measurement: `UNAVAILABLE`; do **not** fabricate a value or
  add a SKIP merely because the optional measurement was missing.
- Existing low-confidence, stale-input, and missed-checkpoint SKIP reasons
  retain priority. The detector never flips the directional model's side.

## What it does and does not establish

This is a **deployed, heuristic risk veto**, not a calibrated probability of a
coin flip and not evidence of improved realized accuracy or positive trade ROI.
A small standardized strike distance signals high *uncertainty relative to
recent BRTI volatility*, not certainty about settlement. The volatility estimate
is backward-looking; real price returns can have drift, jumps, and changing
volatility. The 0.20 cutoff is a conservative initial engineering choice, not a
threshold chosen by independent historical validation.

## Live and historical audit

Every healthy prediction reaching the final gate seals the existing confidence,
`p_yes`, `p_no`, model identity, plus these immutable additional fields:

- `coin_flip_risk_z`: the measured standardized distance, or `null`.
- `coin_flip_assessment`: `HIGH`, `LOW`, or `UNAVAILABLE`.
- `coin_flip_policy`: the exact live policy ID.

The read-only live API and finalized history expose these fields. Legacy records
parse with `UNAVAILABLE` and `null` policy metadata. The live Trade forecast
panel renders the locked risk and z, and AutoTrade's existing final-only engine
blocks orders whenever `forecast == "SKIP"`.

To evaluate effectiveness, compare *future* settled markets by risk category
and track accuracy **together with decision coverage**, missed opportunities,
fees/spreads, realized fill rate, and P/L; do not tune the threshold on a cohort
already used to choose it. A lower conditional error rate with many new skips
is not automatically a better trading strategy.
