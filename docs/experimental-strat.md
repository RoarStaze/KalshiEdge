# Experimental Strat: bidirectional UP and DOWN momentum scalping (paper only)

**State:** isolated experiment; no live Kalshi order submission. In the KalshiEdge localhost dashboard, select **Strat** in the primary navigation or open `http://127.0.0.1:8899/strat`.

This strategy makes **multiple separate UP or DOWN purchases and sales per KXBTC15M market** without consulting the BTC forecast model or confidence gate. It uses the same entry/exit policy on both independent executable bid/ask quote series. Only one side is held at a time; a later round trip may use the opposite side.

## User-defined four trading rules

1. **Only buy when 2:00 or more remains** in the current 15-minute contract. Selling an existing holding remains permissible at any time the market is open.
2. **Only buy UP or DOWN at 65¢–85¢.** Each entry uses that chosen side's executable ask reconstructed from Kalshi's YES/NO order book, not the last completed trade. The paper simulator also refuses any modeled entry fill above 85¢.
3. **Buy on newly accelerating fast momentum in the chosen side.** The same deterministic detector evaluates five consecutive orderbook snapshots covering 2–12 seconds for either UP or DOWN. Both that side's executable bid and ask must rise, each gaining at least 2¢ over the latest two-sample segment, and the bid's recent increase per second must be at least 1.5 times its earlier rate when that earlier rate was positive. **The newest snapshot must also show a fresh bid AND ask increase over the immediately preceding snapshot**: prior-window bursts that have already stalled are rejected. Simultaneous conflicting buy signals cause a skip.
4. **Sell when momentum slows in the side actually held.** A held-side bid reversal or a recent positive held-side bid rate at most 25% of the previous rate triggers an exit. A new faster exit trigger also recognizes when the most recent tick's bid increase falls to half or less of the previous tick's increase, without waiting for the full window to roll over. The exit remains latched through temporary lack of liquidity.

The detector samples approximately every 0.75 seconds via public REST requests. This polling scheme is not an exchange-tick or low-latency momentum feed; missed moves and stale quotes are material limitations.

## How repeated cycles work

- Exactly one paper UP or DOWN position may be open at a time. UP and DOWN are never bought together by this experiment.
- After selling, the scanner may open a new UP or DOWN position when fresh acceleration in that side again satisfies all four rules and the 2:00 or more buy window still remains open.
- A **6-second cooldown** after an exit, **12 paper cycles per market**, a **$2 daily simulated loss circuit breaker**, a **three-consecutive-loss new-entry brake scoped to the current market**, and a maximum per-entry simulated spend of **$0.10–$5.00**, capped at **5% of virtual cash**, prevent runaway simulations. These are risk vetoes, not extra directional trade triggers.
- Every simulated fill/exit, side (UP/DOWN), quantity, cost basis, fee estimate, and hypothetical P&L is stored separately from existing auto trader history in `data/bootstrap/trading/strat_up_paper.sqlite3`. The original file path is intentionally retained so earlier UP-only paper history is preserved.
- Entry prices incorporate **one cent of per-contract slippage**, and exits lose an additional cent. New trades use the **indicative standard Kalshi taker fee formula** rather than the conservative `taker_fee_reserve` safety buffer that had added an unsupported $0.01 per leg. The new estimate rounds cost-plus-fee up to the next **$0.0001** (centicent), using the general 7% fee schedule effective July 7, 2026. This assumes the standard multiplier and a single fill; no individual market/account fee is verified by the simulated book.
- The dashboard displays one-contract **illustrative break-even move**, gross simulated trade-price P&L, estimated entry/exit fees, and net paper P&L independently. The break-even illustration accounts for spread and two one-cent slippage assumptions and is **not a short-term price forecast**.
- **Existing ledger entries are never backfilled or rewritten**. The UI shows old and new trades together with their original fees. For an apples-to-apples historical comparison, export/analyze them by implementation version and fee assumption.
- These changes are improvements to signal timing and accounting transparency, **not empirical proof of positive expectancy**. Testing with real captured quote streams, timestamp-correct replay, and observed manual fill evidence is still required.
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
