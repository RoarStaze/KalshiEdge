# Experimental Strat: bidirectional UP and DOWN momentum scalping (paper only)

**State:** isolated experiment; no live Kalshi order submission. In the KalshiEdge localhost dashboard, select **Strat** in the primary navigation or open `http://127.0.0.1:8899/strat`.

This strategy makes **multiple separate UP or DOWN purchases and sales per KXBTC15M market** without consulting the BTC forecast model or confidence gate. It uses the same entry/exit policy on both independent executable bid/ask quote series. Only one side is held at a time; a later round trip may use the opposite side.

## User-defined four trading rules

1. **Only buy when 2:00 or more remains** in the current 15-minute contract. Selling an existing holding remains permissible at any time the market is open.
2. **Only buy UP or DOWN at 65¢–85¢.** Each entry uses that chosen side's executable ask reconstructed from Kalshi's YES/NO order book, not the last completed trade. The paper simulator also refuses any modeled entry fill above 85¢.
3. **Buy on newly accelerating fast momentum in the chosen side.** The same deterministic detector evaluates five consecutive orderbook snapshots covering 2–12 seconds for either UP or DOWN. Both that side's executable bid and ask must rise, each gaining at least 2¢ over the latest two-sample segment, and the bid's recent increase per second must be at least 1.5 times its earlier rate when that earlier rate was positive. **The newest snapshot must also show a fresh bid AND ask increase over the immediately preceding snapshot**: prior-window bursts that have already stalled are rejected. Simultaneous conflicting buy signals cause a skip.
4. **Sell on confirmed held-side momentum slowdown AND positive after-cost P&L.** The momentum slowdown detector remains unchanged (held-side bid reversal, slower positive rise, or three consecutive flat bid intervals spanning at least two seconds). It now triggers a simulated sale **only if the held-side executable bid minus 1¢ modeled slippage, minus estimated exit taker fee, minus the original total entry cost including entry fees, yields strictly positive dollar P&L**. A positive unrealized quote alone is insufficient without a slowdown. A slowdown that would sell at breakeven or a loss leaves the position open. Actual full-size bid depth is required, and a previously latched positive-profit signal cannot force a later negative sale.

The detector samples approximately every 0.75 seconds via public REST requests. This polling scheme is not an exchange-tick or low-latency momentum feed; missed moves and stale quotes are material limitations.

## How repeated cycles work

- Exactly one paper UP or DOWN position may be open at a time. UP and DOWN are never bought together by this experiment.
- After selling, the scanner may open a new UP or DOWN position when fresh acceleration in that side again satisfies all four rules and the 2:00 or more buy window still remains open.
- A **6-second cooldown** after an exit, **12 paper cycles per market**, exactly one open simulated position at a time, and a maximum per-entry simulated spend of **$0.10–$5.00**, capped at **5% of virtual cash**, constrain paper order volume. They are not extra directional trade triggers. **Neither the number of paper losses in one market nor the day's accumulated hypothetical losses blocks new entries**; both are retained as visible audit metrics. The simulated virtual bankroll remains finite and real orders stay disabled.
- Every simulated fill/exit, side (UP/DOWN), quantity, cost basis, fee estimate, and hypothetical P&L is stored separately from existing auto trader history in `data/bootstrap/trading/strat_up_paper.sqlite3`. The original file path is intentionally retained so earlier UP-only paper history is preserved.
- Entry prices incorporate **one cent of per-contract slippage**, and exits lose an additional cent. New trades use the **indicative standard Kalshi taker fee formula** rather than the conservative `taker_fee_reserve` safety buffer that had added an unsupported $0.01 per leg. The new estimate rounds cost-plus-fee up to the next **$0.0001** (centicent), using the general 7% fee schedule effective July 7, 2026. This assumes the standard multiplier and a single fill; no individual market/account fee is verified by the simulated book.
- The dashboard displays one-contract **illustrative break-even move**, gross simulated trade-price P&L, estimated entry/exit fees, and net paper P&L independently. The break-even illustration accounts for spread and two one-cent slippage assumptions and is **not a short-term price forecast**.
- **Existing ledger entries are never backfilled or rewritten**. The informational cross-market loss streak reads existing paper history (including earlier fee assumptions) but does not prevent new paper entries or rewrite the recorded P&L. The UI shows old and new trades together with their original fees. For an apples-to-apples historical comparison, export/analyze them by implementation version and fee assumption.
- These changes are improvements to signal timing and accounting transparency, **not empirical proof of positive expectancy**. Testing with real captured quote streams, timestamp-correct replay, and observed manual fill evidence is still required.
- Profit-only slowdown exits are **not a guarantee that any trade wins**. When no profitable executable exit becomes available, the paper holding remains open; if the market expires it can only settle using the application's **verified official settlement** helper. It may lose the **entire entry cost** at settlement, and a missing verified settlement can leave the position pending. This version intentionally has **no protective loss-cutting sell**; do not treat lower realized SELL losses as higher economic profitability.
- The **Projected net if sold now** figure is modeled against the most recently observed held-side executable bid, available full-size bid depth, estimated fees and 1¢ exit slippage. A live order could receive a different price or no fill at all. The paper ledger records only strictly positive net P&L on slowdown sales; real settlement losses are preserved rather than rewritten.
- Pausing disables new entries but keeps observing open paper positions. A restart always disables new entries until the user re-arms.

## Profit-gated exit experiment

The user-requested exit variant requires **both** (1) a held-side slowdown
signal and (2) an estimated strictly positive full-size executable paper
exit after all modeled costs. This changes **exit timing only**, not the
existing UP/DOWN entry signal, the >=2:00 new-entry rule or 65–85¢
entry range. It does not fix the historically negative signal expectancy.
Momentum can reverse without ever offering a profitable exit, causing
the full paper position to remain open until official verified settlement.
There is no stop-loss in this experiment. The project retains all old
losing paper trades and does not backfill old outcomes.

This is an intentionally high-risk *paper-only* counterfactual: deciding
not to recognize a loss today does not eliminate the risk of a loss later.
It should be evaluated on full lifecycle P&L including all settlement
outcomes, capital tied up, and trade coverage—not just the percentage
of completed profit-taking exits.

## Audit capture and limitations

**The current four-rule signal has not demonstrated a positive after-fee edge.**
Repeated losing simulated trades indicate that reacting to an already-observed
2c burst often fails to produce enough *future* continuation to pay for the
round trip. Tightening the entry rule, changing fees or holding losers longer
cannot establish a profitable strategy without timestamp-correct evidence.

While the paper experiment is armed, every validated market poll writes one
immutable row with simultaneous UP/DOWN executable bids, asks, displayed depth,
the market and close timestamp, time remaining, held side and the reason for
each directional signal. The observation timestamp is taken **after the
market-data HTTP response arrives** and is used for the 2:00 entry gate and
simulated fill times, rather than the time before the request. This is a client
receipt timestamp, **not** an exchange-event timestamp; server/network delays
still limit scalp timing reliability. The local file is
`data/bootstrap/trading/strat_quote_tape.sqlite3`.
The quote recorder continues during ordinary entry restrictions (such as the
current market's loss brake or the daily dollar loss limit), so candidate
signals can still be reviewed without creating additional paper losses.
New entries are blocked if evidence cannot be written to disk; open positions
remain monitored. Pausing entries intentionally stops quote polling when flat,
and restarting the app still requires manually rearming paper Strat.

The same-day consecutive loss streak **across tickers** is informational only
in this paper-only experiment. It no longer blocks new trades across all
markets after three losses, which previously left Strat showing
`GLOBAL_LOSS_CIRCUIT_ACTIVE` and unable to trade for the rest of that UTC day.
**The former same-market three-loss brake and the $2 daily paper-loss
stop have also been removed from entry authorization.** Their historical
loss and streak metrics remain intact; no ledger rows are removed or
recalculated. There is no way to enable real orders through Strat.
The 12-cycle market cap, simulated budget/virtual cash constraints, and
6-second re-entry cooldown continue to apply. The UI prioritizes the
current market's 2:00 entry cutoff, then the executable ask window,
before displaying any other entry restriction. It separately reports
the raw UP/DOWN momentum decision and informational loss history. The Strat page has an **Analyze captured momentum** button. It opens a local,
read-only endpoint (`/api/strat/replay`) and compares prior unheld BUY signals
with strictly subsequent executable best bids at 2, 5, 10 and 20 seconds
(allowing at most three seconds of quote collection jitter). The report assumes
one-contract taker entry at ask+1c and liquidation at bid-1c with the same
indicative standard fee estimate used by the paper engine. Missing future
quotes, insufficient candidates (fewer than 30), and a truncated sample
(first 5000 historical candidate records) are identified explicitly. Overlap
between time horizons or repeated candidate signals makes estimates correlated.
No replay result is evidence of a predictive trading edge: there is **no
holdout-tested conditional return estimator, real fill reconciliation, or
verified maker execution**. Re-evaluate on subsequent untampered data rather
than optimizing against the same known losses. Reconstructing actual maker fills
requires independent trade and order-queue evidence not contained in REST
orderbook snapshots.

No change authorizes real-money orders.

## Historical negative-taker evidence gate

The current fast momentum BUY signal has repeatedly shown negative *modeled
taker* markouts. Strat now compares each side (UP and DOWN) against only
**previously completed markets** at the start of each new market. The
side-specific paper entry veto is activated when the recorded 20-second
forward markouts contain at least **60 matched candidates** from at least
**10 earlier closed markets**, with at least **80% quote coverage**, at most
**30% positive modeled taker markouts**, and average after-cost return of
**−2¢ per contract or worse**. The current market never contributes to
its own gate. Incomplete history keeps the previous exploratory paper
behavior; it is **not** positive evidence. Exits for existing positions
continue regardless of the gate.

**Paper research opt-in:** The Arm controls now have an unchecked
`Paper exploration — optional negative-evidence override` checkbox.
When deliberately enabled, this option bypasses **only the negative
historical taker entry veto**. It never bypasses the 2:00 entry cutoff,
65–85¢ executable ask requirement, acceleration, budget, single open
position, liquidity, re-entry cooldown or market cycle limits. Trades
remain 100% simulated. Historical negative results continue to be shown
and are not recalculated or deleted. The option must be explicitly
selected again each time entries are armed, is cleared on pause and on
application restart, and cannot authorize any real orders.

**Example:** with 7:33 remaining, UP ask 56¢, DOWN ask 45¢, **neither
side qualifies** even if the exploration override is selected. Once a
side's executable ask enters 65–85¢, it still needs a fresh accelerating
BUY signal and the modeled entry price cannot exceed 85¢.

These fixed thresholds are deliberately conservative *negative-evidence
heuristics*, not significance tests, optimized settings or an automatic
profitability filter. Correlated events, exact fill availability, contract
fees and maker queue positions are unresolved. No real orders are enabled.

The replay panel also reports an **optimistic, explicitly unverified maker
entry scenario**: it assumes an immediate fill at the contemporaneous
best bid, zero maker entry fee, then the same future taker liquidation as
before. This assumes fillability without any proof and can greatly
overstate attainable returns. A positive optimistic result is NOT a
profitable strategy or evidence that a resting order would have filled.
The baseline displayed taker markouts remain unchanged.

## Architecture and safety

- Signal: `src/kalshi_edge/trading/strat_policy.py` (pure deterministic decision; no network).
- Paper executor + durable ledger: `src/kalshi_edge/trading/strat_paper.py`.
- Causal timestamped quote store: `src/kalshi_edge/trading/strat_tape.py`.
- Read-only forward-quote audit: `src/kalshi_edge/trading/strat_replay.py`.
- Prior-closed-market negative-taker entry guard: `src/kalshi_edge/trading/strat_evidence.py`.
- Local dashboard: `src/kalshi_edge/bootstrap/strat_ui.py` at `/strat`.
- Localhost-only read endpoints: `/api/strat/status` and `/api/strat/replay`; same-origin action endpoints: `/api/strat/arm` and `/api/strat/pause`.
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

Then select **Strat**, choose the simulated budget, and press **Arm paper Strat**.
By default the negative historical entry veto is enforced. To collect
further *paper-only* trades despite already-negative historical evidence,
deliberately check **Paper exploration** before arming. The checkbox
is not an order permission, cannot make a 56¢ or 45¢ ask eligible under
the 65–85¢ rule, and does not imply the strategy will profit.
Nothing places a real Kalshi order. Keep the server running to record real-time simulated cycles.
