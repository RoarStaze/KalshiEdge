# Independent intraround UP/DOWN paper scalper

## Problem
Paper scalp worker currently requires a 480-second final forecast and >69% probability. This forces an 8-minute wait and the final side can exclude opposing short-term price movements. The user wants continuous, direction-agnostic intramarket scalping.

## Scope
**Paper-only** BTC 15-minute KXBTC15M. No modification to production `ArmedAutoTrader`, final forecast policy, confidence gate, credential access, or real trades. Existing durable SQLite simulated cycle ledger, restart position recovery, cash/risk limits and public quote client remain.

## Signal
- Observe active KXBTC15M public YES+NO bids on each existing 3-second paper poll. One fetch per poll, normalize both UP/YES and DOWN/NO executable best bid and derived best ask using `quote_from_kalshi`.
- Require the currently fresh local live market identifier (`status=LIVE`, ticker match, prediction record age 0-8s, market close in the next 15 minutes). A fresh market ID is not a final forecast or model direction gate. No use of checkpoint, final forecast probability, model confidence, or settlement forecasts in *entry decisions*.
- Maintain independent, ordered 3-observation histories for UP and DOWN. Over 2-15 seconds, require each of bid and ask to trend strictly the same way (up for the purchased side), min best-bid movement >= 3¢, spread <=5¢, sufficient visible bid + ask top-level depth to support quantity, no stale or crossed quote.
- Apply 1¢/contract hypothetical slippage to both entry and candidate exit plus both taker fee reserves and reject positions whose immediate liquidation cost would breach the existing 8% / 2¢ hypothetical stop. Require bounded roundtrip friction. Signal is a *hypothesis*, never a profitable forecast.
- If both sides qualify at once, reject as ambiguous rather than choosing arbitrarily.
- Track side on each paper cycle; after exit, resume scanning both directions after 10-second cooldown, max 12 modeled cycles per market and existing daily risk limits. No opposite-side switch while OPEN.
- Exit before settlement if modeled sell-at-best-bid **after 1¢ simulated slippage and fee reserve** yields >= 1¢ net profit, rather than waiting for a 5% profit threshold. Retain existing stop-loss, trailing and time exits. Exit only if sufficient public bid depth; no invented early fill.
- Late/no-feed/no-depth conditions fail closed; an open position retains its own ticker and remains monitored until safe hypothetical exit or independently verified settlement. Restart resumes monitoring open position and disables new entries.

## UI and diagnostics
Paper panel status explicitly says **INDEPENDENT UP/DOWN MOMENTUM — NO FINAL FORECAST REQUIRED**, polling about every 3 seconds. Status API identifies `entry_policy=INTRAROUND_UP_DOWN_BOOK_MOMENTUM`, `live_order_submission=false`, `paper_minimum_net_profit_dollars=0.01`, scan cadence, and informative `last_signal` codes. No language implying guaranteed fills, profit or that every tick is observed.

## Verification
Offline tests must demonstrate:
1. Paper entry occurs when the 480s final is absent and market still has >480s to go;
2. UP vs DOWN selection from matching signed movement, independent of opposite final;
3. No entry if only model probability changes or market quotes flat;
4. Block stale/missing/conflicting market identifiers, crossed books, weak momentum, thin depth, fee/slippage stop breach;
5. Exit promptly at >=1¢ net profit and never at paper mark/midpoint without executable depth;
6. Persistence/restart/cooldown/multiple per-market entries, daily risk controls, no live order submissions;
7. Existing >69% real AutoTrade final unchanged. Full repo CI and container green.

## Evidence and limitations
Kalshi orderbook bids provide executable *top-of-book indications*, not guaranteed fills. Small-stake fees, spreads and orderbook latency can dominate expected gain. A 3-second REST poll can miss sub-second spikes. No verified prospective statistical edge from this signal. No real-money activation.
