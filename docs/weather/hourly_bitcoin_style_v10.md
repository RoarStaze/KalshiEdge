# WeatherEdge V10 — Bitcoin-style Weather Trade page

## Approved visual target
Use the user's November 27 Bitcoin Trade screenshot and approved Weather mockup as a design/layout target. Recreate the same hierarchy in the **actual** `/weather` page: slim header; two equal-height 50/50 neon glass outer panels; left hourly forecast, official index, threshold, countdown, model lean/probability, live flip watcher; right trading-shaped *read-only* Kalshi contract view and actual exchange ask data. Preserve existing dashboard background and moving rainbow outer borders (never nested borders). Responsive single-column stacking under 920 px.

## Source truth and safeguards
- This is a visual redesign, not permission to enable weather order placement.
- Never show demo prices, fictitious balance, fake 8-minute record, fake risk score, or unsupported "LIVE ACCOUNT"/"Buy 1-Click".
- Target threshold is a listed Kalshi **YES <threshold>°F or above** strike, not an arbitrary meteorological forecast. Current index is published **Kalshi Weather Index**, not a station temperature.
- Forecast direction and confidence are research estimates for the **selected specific ticker** only, not a profit guarantee, historical accuracy, or trading signal.
- Exchange YES/NO asks may appear only if actually present in validated source evidence, quote status is VALID_ASK, and the receipt is fresh. Do not imply those asks are executable or that fees are included.
- Close countdown must use server-published per-contract close_time_utc, never target_utc; missing closes show "CLOSE UNKNOWN" and stop pretending the market is open.
- "Last forecast updated" uses actual read-only asof; "Reversal risk" shows NOT VALIDATED; live page-local flip monitor may observe side changes for one city/event/ticker but does not issue notifications or persist as validated model.
- Show paper estimate for entered dollar amount from current fresh YES/NO ask only; label before fees, never infer order quantity from a synthetic quote. Disable checkout.
- Keep all existing advanced hourly science, 5/15/30/45/60-minute V9 calibration, feed diagnostics, backend APIs, Train/History/Feeds sections. Only rewrite foreground Weather Trade layout.
- Header nav maps existing routes into Trade, Train, History, Feeds. No broken Strat/API placeholders.

## Acceptance
Screenshots approximately align in shape and colors with user-selected mockup, with safe true data. No added POST endpoints, no live weather execution, no BTC/Gold modifications. JavaScript syntax valid; test first (RED), then full GitHub CI and Docker build (GREEN).
