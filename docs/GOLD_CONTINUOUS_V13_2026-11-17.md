# GoldPredict AI V1.3 — continuous market evidence (research-only)

Updated 2026-11-17. KXGOLD15M is isolated from Bitcoin and all order-execution logic.

## Run on Windows PowerShell

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = ".\.venv\Scripts\python.exe"
& $py -m pytest -q tests/gold

# Continues while the terminal stays open; Ctrl+C stops.
& $py -m kalshi_edge.gold --collect-continuously --interval-seconds 30 --output-root data/gold
```

**Safety trial:** run two cycles, then the command exits by itself:

```powershell
& $py -m kalshi_edge.gold --collect-continuously --interval-seconds 30 --max-cycles 2 --output-root data/gold
```

**Inspect progress later, even after the collector stopped:**

```powershell
& $py -m kalshi_edge.gold --collector-status --output-root data/gold
```

One collector process per `output-root`. The OS-held lock is released on normal exit, Ctrl+C and crashes; the lock file may remain as an inert marker. Restart by rerunning the same command; old SHA-256 evidence and cycle files remain intact. There is no built-in background scheduling or auto-start after a Windows reboot.

## Collected sources and auditability

Each cycle performs public Kalshi REST GETs only:

- `/series/KXGOLD15M` and `/markets?series_ticker=KXGOLD15M&status=open`: full decoded quote and market payloads, UTC request/receipt nanoseconds and server Date when supplied. Stored under `data/gold/prospective_quotes/`, using the pre-existing V1.2 capture schema.
- `/markets/{ticker}/orderbook?depth=10`: fixed-point YES/NO bid depth per sampled open market.
- `/markets/trades?ticker={ticker}&limit=100`: recent trades; pagination cursors are preserved with an explicit `pagination_capped` marker, **not silently treated as complete**. Up to 2 markets per cycle by default, 8 maximum. Stored under `data/gold/gold_microstructure/` with a backlink to the quote artifact's SHA-256.
- `data/gold/collector_events/`: an immutable, hash-named event per cycle, including failure reason, paths to successful partial evidence and session event-digest chain. `--collector-status` verifies the event files and summarizes collected/failed cycles.

Poll interval: 30 seconds by default, permitted 10–3600 seconds. Network failures, malformed responses and HTTP 429 cause recorded failures and exponential backoff capped at 300 seconds. Keep a terminal open; choose a larger interval if rate limits appear.

The collector reuses one HTTP client per process, but orderbook and trade pages are sequential **not atomic snapshots**. Kalshi market-listing data may be delayed. Local timestamps establish receipt at the machine—not independently authenticated exchange publication or first availability. Future gold training requires clock checks and as-of provenance beyond these receipts.

## Settlement-verification track (still blocked)

A collector run **does not** authenticate Pyth Pro `Metal.Index.1OZGOLD/USD`, observe an authoritative settlement feed, establish market-specific open/end minute labels, resolve mixed EST/EDT terms or reconstruct final YES/NO outcomes.

V1.2 already includes the offline `--import-pyth-history` and `--verify-settlement-evidence` commands. Their verified label count remains zero until independent Pyth origin/channel attestation and precise contract-time evidence exist. A local SHA-256 proves integrity, not Pyth authenticity. An authenticated Pyth history request is explicitly separate from this public-only collector and requires authorized credentials **outside this feature**; never commit API tokens or history private credentials.

When externally obtained authorized source evidence becomes available, use `docs/GOLD_SETTLEMENT_ORACLE_V12_2026-11-17.md` to import it and run the dedicated settlement audit. Quarantine timezone, source, tie, rounding, gap and reported-outcome disagreements rather than choosing convenient labels.

**No gold training model, live predictor, live trading, backtest performance or independent Pyth labels have been added or enabled by V1.3.**

## Official sources

- https://docs.kalshi.com/api-reference/market/get-markets
- https://docs.kalshi.com/api-reference/market/get-market-orderbook
- https://docs.kalshi.com/api-reference/market/get-trades
- https://docs.kalshi.com/getting_started/rate_limits
- https://docs.pyth.network/api-reference/historical-prices/history

## Verify locally

```powershell
& $py -m pytest -q tests/gold
& $py -m pytest -q
```
