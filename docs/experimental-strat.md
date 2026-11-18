# Experimental Strat: UP momentum scalping (paper only)

**State:** isolated experiment; no live Kalshi order submission. In the KalshiEdge localhost dashboard, select **Strat** in the primary navigation or open `http://127.0.0.1:8899/strat`.

This strategy makes **multiple separate UP purchases and sales per KXBTC15M market** without consulting the BTC forecast model or confidence gate.

## User-defined four trading rules

1. **Only buy when 7:00 or more remains** in the current 15-minute contract. Selling an existing holding remains permissible at any time the market is open.
2. **Only buy UP at 65¢–85¢.** The entry eligibility test uses the executable UP ask reconstructed from Kalshi's YES/NO order book, not the last completed trade. The paper simulator also refuses any modeled entry fill above 85¢.
3. **Buy on newly accelerating fast UP momentum.** The initial deterministic detector evaluates five consecutive orderbook snapshots covering 2–12 seconds. Both executable bid and ask must rise, each gaining at least 2¢ over the latest two-sample segment, and the bid's recent increase per second must be at least 1.5 times its earlier rate when that earlier rate was positive.
4. **Sell when UP momentum slows.** A bid reversal or a recent positive bid rate at most 25% of the previous rate triggers an exit; the exit remains latched through temporary lack of liquidity.

The detector samples approximately every 0.75 seconds via public REST requests. This polling scheme is not an exchange-tick or low-latency momentum feed; missed moves and stale quotes are material limitations.

## How repeated cycles work

- Exactly one paper UP position may be open at a time.
- After selling, the scanner may open a new UP position when fresh acceleration again satisfies all four rules and the original buy window still remains open.
- A **6-second cooldown** after an exit, **12 paper cycles per market**, a **$2 daily simulated loss circuit breaker**, and a maximum per-entry simulated spend of **$0.10–$5.00**, capped at **5% of virtual cash**, prevent runaway simulations. These are risk vetoes, not extra directional trade triggers.
- Every simulated fill/exit, quantity, cost basis, fee estimate, and hypothetical P&L is stored separately from existing auto trader history in `data/bootstrap/trading/strat_up_paper.sqlite3`.
- Entry prices incorporate **one cent of per-contract slippage**, and exits lose an additional cent, with pessimistic fee reserves.
- Stop/slowdown signals are not execution guarantees. If liquidity is absent, the paper position stays open; if the market expires it can only settle using the application's **verified official settlement** helper.
- Pausing disables new entries but keeps observing open paper positions. A restart always disables new entries until the user re-arms.

## Architecture and safety

- Signal: `src/kalshi_edge/trading/strat_policy.py` (pure deterministic decision; no network).
- Paper executor + durable ledger: `src/kalshi_edge/trading/strat_paper.py`.
- Local dashboard: `src/kalshi_edge/bootstrap/strat_ui.py` at `/strat`.
- Same-origin, localhost-only HTTP controls: `/api/strat/status`, `/api/strat/arm`, and `/api/strat/pause`.
- Regression coverage: `tests/trading/test_strat.py`.

**Important:** No signed client, account balance, authenticated positions, or live order POST is used by Strat. The separate preexisting manual trading and final-forecast AutoTrade routes are not modified by the experiment. Before authorizing real-money mode, validate representative live orderbook capture, actual maker/taker execution, partial fills, order reconciliation on unknown outcomes, and net after-fee P&L. Simulated price paths are not evidence of profitability.

## Run and test

After updating the repository on Windows PowerShell:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m pytest -q tests/trading/test_strat.py
kalshiedge start
```

Then select **Strat**, choose the simulated budget, and press **Arm paper Strat**. Nothing places a real Kalshi order. Keep the server running to record real-time simulated cycles.
