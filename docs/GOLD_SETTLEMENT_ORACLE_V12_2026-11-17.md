# GoldPredict AI V1.2 — settlement oracle and historical evidence verification

**Scope:** `KXGOLD15M` only. **Research only. No credentials, live
inference, training, or orders.** Revised 2026-11-17.

## Verified official source facts

1. Kalshi's generic COMMODITIES rulebook defines *at least* as inclusive
   (≥) and specifies that rounding and price precision can be set per
   issuance. These generic terms are NOT sufficient to determine the exact
   start/end 1-minute Pyth candle for each KXGOLD15M contract.
   Primary document: https://assets.kalshi.com/contract_terms/COMMODITIES.pdf
2. Kalshi confirms that the full text of each contract, not merely a
   summary, is the controlling market-specific disclosure:
   https://help.kalshi.com/en/articles/13823823-rules-summary
3. Kalshi's KXGOLD15M series reports the source identifier
   `Metal.Index.1OZGOLD/USD`; the repo's existing source-identity
   validation already insists on that exact symbol, not a substitute XAU/USD.
   Exchange series: https://external-api.kalshi.com/trade-api/v2/series/KXGOLD15M
4. Pyth documents `GET https://pyth.dourolabs.app/v1/{channel}/history`
   with parameters `symbol`, `from`, `to`, `resolution`, and an
   `Authorization: Bearer <token>` header. Use `resolution=1` for
   one-minute OHLC data. Its `t` timestamps and `c` closing prices are
   aligned by array index. **These docs do not establish Kalshi's exact
   candle-label mapping or which Pyth channel was used historically**.
   https://docs.pyth.network/pro/api/rest
   https://docs.pyth.network/api-reference/historical-prices/history

## Existing unresolved blockers

- Market-specific KXGOLD15M `rules_primary` text may inconsistently use
  `EST` and `EDT` in the same contract. **Never** repair mixed zones by
  inferring from the ticker, `close_time`, or a fixed UTC offset.
- Contract opening and closing candlestick observations are not proven by the
  Kalshi market's API `close_time` field alone. The eight-minute
  `close_time - 480s` checkpoint remains a hypothesis, not certification.
- Whether Pyth OHLC `t` labels identify the minute start or the settlement
  minute end must be independently confirmed with genuine market evidence.
- Official Kalshi `result=yes/no` is not itself an independently
  reconstructed Pyth index label.
- Authenticated Pyth history requires externally obtained access. V1.2 does
  not read keys, send bearer tokens, or call the authenticated endpoint.
- Pyth history values and receipt times in a local file can be forged. A
  SHA-256 hash verifies file integrity but cannot prove that Pyth published
  the values. A later adjudicated/signed primary-source protocol is required.
- There are still no verified prediction-time feature publication timestamps
  to authorize backtesting or XGBoost training on these gold examples.

## V1.2 implementation

**Offline Pyth evidence importer:** `gold/oracle_history.py` accepts a
user-supplied Pyth REST history JSON response; it does NOT download prices,
claim API authentication or need a key. It validates bounded one-minute
timestamps, increasing order, aligned OHLC arrays, positive finite prices,
nonnegative volume and high/low consistency. It preserves the original
UTF-8 JSON exactly inside a new immutable SHA-256-named evidence artifact
and records the exact source SHA-256 plus an HTTP request plan containing
the symbolic feed, channel and timestamp range. All captured origins are
marked `origin_authenticated=false`, all labels `settlement_verified=false`.

**Settlement evidence review:** `gold/settlement_evidence.py` joins a
hashed Kalshi inventory with hashed Pyth-format evidence and checks per-
ticker contradictory reported YES/NO, nonfinalized markets, missing rule
text and mixed EST/EDT abbreviations. It records a reason for each market
and source hashes. It **intentionally performs no independent outcome
comparison** and emits no verified labels until the Pyth channel, candle
alignment and contract time rules have real evidence. The output is
`training_eligible_labels=0` and `dataset_ready=false`, even when OHLC
data exists.

**Dataset readiness:** `--audit-dataset` accepts an optional
`--oracle-history-artifact`; it records that artifact's SHA-256, its
unattested one-minute bar count and an explicit
`pyth_history_import_unattested` blocker. It cannot promote any rows.

## How to use after pulling latest main

First, obtain an authorized Pyth Pro OHLC export **outside KalshiEdge**
using Pyth's own documentation and access controls. Preserve the JSON body,
its channel (`real_time` or `fixed_rate@200ms`), original query `from`,
`to` Unix seconds and an independent trustworthy receipt record. Do not
paste private API keys into chat or GitHub. The channel is not assumed to
be the one Kalshi uses for settlement.

For a downloaded `pyth-history.json` with an exact, known request range:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Use the actual from/to Unix seconds from YOUR Pyth request.
& $py -m kalshi_edge.gold --import-pyth-history "pyth-history.json" `
  --pyth-from-ts 1794900000 --pyth-to-ts 1794900180 `
  --pyth-channel real_time --output-root data/gold
```

**The timestamps above are a synthetic CLI example, not a real
historical coverage claim.** Replace them with the exact request range.

For a hashed imported artifact and your existing inventory:

```powershell
$inv = Get-ChildItem "data\gold\market_inventories\*.json" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
$oracle = Get-ChildItem "data\gold\pyth_history_imports\*.json" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not $inv -or -not $oracle) {
  throw "Both Kalshi inventory and imported Pyth OHLC evidence are required"
}

& $py -m kalshi_edge.gold --verify-settlement-evidence $oracle.FullName `
  --inventory-artifact $inv.FullName --output-root data/gold

& $py -m kalshi_edge.gold --audit-dataset $inv.FullName `
  --oracle-history-artifact $oracle.FullName --output-root data/gold

& $py -m pytest -q tests/gold
```

Source files are not altered. A separate hashed artifact appears under
`data/gold/settlement_evidence/`. The count of **verified gold labels
remains zero**, not because the Pyth index necessarily disagrees with
Kalshi but because the independent evidence gate has not been satisfied.
Do not train XGBoost or deploy gold trading using this artifact.

## Evidence needed to unblock real settlement reconstruction

1. Obtain trustworthy, complete historical Pyth OHLC **and its original
   authenticated request/channel or signed source receipts**, covering
   the true start/end minutes, not merely hypothetical market close.
2. Confirm the precise Kalshi market-specific contract text and how
   minute labels map to candle start/end, Eastern timezone, DST changes,
   rounding and fallback policies. Preserve the verified rule version.
3. Independently resolve the suspect EST/EDT instances with authoritative
   clarifications rather than silently choosing an offset.
4. Reconstruct eligible market outcomes from vetted source prices,
   flag ties, revisions, missing bars and corrections, and compare with
   genuinely finalized Kalshi results; quarantine mismatches.
5. Collect contemporaneous gold market features with receipt/publish
   timestamps before each chosen forecast checkpoint. Freeze time-
   separated training/calibration/test partitions with an untouched
   chronological lockbox. Only then train a research XGBoost baseline.

**Outcome of V1.2:** verified source-documentation and a **fail-closed
evidence ingestion and audit pipeline**, not a trained or independently
reconstructed gold settlement dataset.
