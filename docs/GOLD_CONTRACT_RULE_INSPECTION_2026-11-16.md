# KXGOLD15M Stage 1b: inspect the 2026-11-16 contract snapshot

**Scope:** Read-only analysis of the exact JSON returned by `python -m kalshi_edge.gold`. No extra API requests, subscriptions, training, model promotion, trading or Bitcoin configuration changes.

## Run on the Windows host

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m kalshi_edge.gold --inspect-artifact "data\gold\contract_audits\1794894408078744900-12b9041d0b3c206f.json"
& .\.venv\Scripts\python.exe -m pytest -q tests/gold
```

The inspector verifies the SHA-256 prefix in the immutable snapshot filename and the exact KXGOLD15M/Pyth source identity. It prints each sampled contract's **unaltered** primary and secondary rule text, rule-text hash, API close/expiration UTC timestamps, and an **unapproved** 480s checkpoint calculated from API `close_time`. The report flags mismatched `EST`/`EDT` mentions, missing rule text, missing or invalid timestamps, duplicate tickers, and expiration preceding close. It never creates a settlement label, uses a substitute Pyth index, or marks the dataset training-ready.

**Known evidence:** Kalshi's generic COMMODITIES terms define commodity comparison and fallback rules, but the exact gold series market rules remain the controlling per-contract interpretation. A generic `above` operator is strictly greater; actual sampled KXGOLD15M rules publicly displayed elsewhere use `at least`, which is inclusive. Do not pick a rounding mode based solely on the phrase "nearest 2 decimals." The minute label (example: candle labeled 4:59 may close at 5:00) must be matched to actual Pyth candle publication and official results.

- Gold series: https://external-api.kalshi.com/trade-api/v2/series/KXGOLD15M
- Kalshi commodity terms: https://assets.kalshi.com/contract_terms/COMMODITIES.pdf
- Gold example with conflicting EST/EDT: https://www.eventquant.com/charts/market/KXGOLD15M-26NOV050300-00/

**Outstanding:** inspect real file output and determine exactly which of the five sampled markets have currency, rule-version or timezone mismatches; paginate across recent and archived contracts; verify price observation times and official outcomes against a licensed exact-index recording. Report `settlement_verified=false` and `training_ready=false` until those checks are evidenced. Reported rule variants mean **exact text variants**, not semantic rules.

## Data and hardware reminders

The Stage 1 API returned 5 sample markets and a next-page cursor. It is not a complete historical audit, and does not provide an exact historical Pyth feed. The user's Intel i5-13420H (12 logical processors) and NVIDIA RTX 4050 laptop (6141 MiB reported) are sufficient for baseline development; system RAM remains to be read:

```powershell
[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
```

Do not clean or reset the working tree: the host already reports local `src/kalshi_edge.egg-info/` modifications, a TimesFM virtualenv, `tools/` and several `.bak` files.
