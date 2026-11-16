# Read-only BRTI joint confirmation and historical false-SKIP audit (2026-11-16)

## Why this exists

The prior local **123-settled-market** report identified:
- 56 originally **issued** predictions: 52 correct, 4 incorrect (92.86% conditional accuracy and 45.53% *historical issuance* coverage).
- 67 originally **skipped** model directions: 44 counterfactually correct, 23 incorrect (65.67%). All 67 carried **legacy 63% / 69% model-strength** SKIP reasons, not today's >63% predicted-side-probability policy.
- Across all 123 saved checkpoint model directions: 13 had saved normalized BRTI risk z <=0.20, 17 had BRTI/Binance prices on opposing sides of the strike, but these are **not individual live vetoes**.
- The **joint distributions** (legacy SKIPs with z>1, both reference feeds agreeing, and model-side confirmation) were not available from the aggregate report.

**Consequently, the old 45.53% issuance coverage is NOT measured live coverage under the current 63% directional gate.** Choosing an arbitrarily lower confidence threshold or more indicators cannot establish a true accuracy and coverage improvement.

## What it actually does

`python -m kalshi_edge.bootstrap.brti_joint_rescue_audit_480` uses only existing:
- SHA-256 model-hash-matched, **immutably locked 480-second final forecast** records;
- separately saved **official Kalshi YES/NO settlements**;
- hash-verified, strictly causal forward feature snapshots, captured at the exact forecast checkpoint, containing BRTI-to-strike bps, Binance spot, cross-reference bps, and original timestamps;
- the original model probability and originally captured normalized BRTI strike-distance risk-z.

It reports:
1. Historical issued vs **legacy strength-SKIP** accuracy, with the appropriate denominators.
2. Legacy strength-SKIPs with **z > 1.0 and BRTI/Binance on the same strike side**, with how many of the saved **model directions** were retrospectively correct/wrong.
3. The subset where **both benchmarks additionally agree with the model direction** (not just with each other), and the subset where references agree **against** the model.
4. How many of those hypothetical joint cases are *already above* today's 63% predicted-side-probability gate versus **at or below** it. Cases at/below that gate must not be called automatic eligible trades.
5. A separate **counterfactual replay** of the active strict >63% probability gate *and the recorded BRTI z<=0.20 veto* on the same saved model outputs. This is **not** the historically issued-decision rate or a substitute for new real-time tracking.
6. The joint signals broken out by *predeclared model probability intervals* [0.50,0.63], (0.63,0.70], (0.70,0.80], (0.80,1.00] to distinguish reference effects from probability selection.
7. How often BRTI/Binance agree, disagree or have insufficient historical evidence; invalid and stale records are never silently backfilled.

**Causality / integrity:** a usable reference snapshot must have separately recorded BRTI and Binance source times **no more than five seconds old**, valid digests, non-tied strike-side observations, and internally consistent BRTI/Binance basis. Missing, stale, inconsistent or unrecorded data produces `unavailable`, not a manufactured signal. No future price, outcome or settlement is ever used to *select* an eligible cohort.

## Run on the user's Windows laptop

Preserve existing local changes to `src/kalshi_edge.egg-info/`, backups and untracked virtual environments before updating. Do not run `git reset --hard` or `git clean` to force a pull.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"

# Joint z>1 / BRTI-Binance agreement / model-side confirmation:
& $py -m kalshi_edge.bootstrap.brti_joint_rescue_audit_480

# Complete machine-readable metrics, exclusions, conditional probability bins:
& $py -m kalshi_edge.bootstrap.brti_joint_rescue_audit_480 --json

# Synthetic deterministic evidence integrity and outcome-accounting tests:
& $py -m pytest -q tests/bootstrap/test_brti_joint_rescue_audit_480.py
```

If the local active model changes, use the original hash to avoid mixing different models:

```powershell
& $py -m kalshi_edge.bootstrap.brti_joint_rescue_audit_480 --model-hash 5c249cd522d23b77db85670b21adc3b516e052278510b2f556b4764679c22657
```

## Strict interpretation

The **legacy** historical-strength SKIPs are not skips under the *current* directional-probability policy. Many previously skipped markets can already qualify at 63% under the newer rule without any change to the model. **No automatically rescued forecasts** are created by this command.

All outcomes were already inspected repeatedly in past research. This experiment is **descriptive discovery** and does not offer independent validation of a BRTI-aware policy, coefficients, accuracy improvement, coverage gain, better calibration, or profits. A promising conjunction would need to be frozen *before* evaluating consecutive fresh settlements and checked against the real deployed 63%-probability-plus-BRTI-veto incumbent.

This script never retrains, changes the trained model probability, changes the 480s checkpoint or gate, disables the BRTI veto, alters AutoTrade, calls an exchange, or writes to immutable data.
