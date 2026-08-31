# Current-Source Verification Report

**Verification date:** 2026-08-31

This report is the source-verification baseline for Phase 1. Current official documentation overrides this file if the API changes later.

## VERIFIED FACTS

1. **KXBTC15M series.** The live series endpoint reports ticker `KXBTC15M`, frequency `fifteen_min`, category `Crypto`, settlement source `CF Benchmarks`, `fee_type=quadratic`, `fee_multiplier=1`, and `exchange_index=2`. The series metadata says that during the final minute, 60 RTI prices are collected and averaged for the official value. Source: https://external-api.kalshi.com/trade-api/v2/series/KXBTC15M
2. **Production REST base.** Public prediction-market data is served from `https://external-api.kalshi.com/trade-api/v2`. Source: https://docs.kalshi.com/getting_started/quick_start_market_data
3. **Production WebSocket.** The dedicated production endpoint is `wss://external-api-ws.kalshi.com/trade-api/ws/v2`; demo is `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`. Source: https://docs.kalshi.com/getting_started/quick_start_websockets
4. **WebSocket authentication.** The handshake uses `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, and `KALSHI-ACCESS-TIMESTAMP`. The signed string is timestamp + uppercase HTTP method + path without query parameters. RSA-PSS with SHA-256 is documented. Sources: https://docs.kalshi.com/getting_started/api_keys and https://docs.kalshi.com/getting_started/quick_start_websockets
5. **Order book channel.** `orderbook_delta` sends an `orderbook_snapshot` first and incremental `orderbook_delta` messages afterward. The channel carries `sid` and `seq`; market filters use ticker(s). Source: https://docs.kalshi.com/websockets/orderbook-updates
6. **Order book semantics.** Kalshi exposes YES and NO bids; implied YES ask is `1 - best NO bid`, and implied NO ask is `1 - best YES bid`. Source: https://docs.kalshi.com/getting_started/orderbook_responses
7. **Public trades.** The `trade` channel emits trade ID, market ticker, YES/NO prices, fixed-point count, taker side, and timestamps immediately after execution. Source: https://docs.kalshi.com/websockets/public-trades
8. **CF Benchmarks feed.** `cfbenchmarks_value` supports `index_ids=["BRTI"]`, emits roughly once per second, includes the raw upstream frame and a trailing 60-second average, and exposes `last_60s_windowed_average_15min` only during the final minute before each quarter-hour close. Source: https://docs.kalshi.com/websockets/cfbenchmarks-value
9. **Lifecycle feed.** `market_lifecycle_v2` receives all market lifecycle events and can expose creation/activation/settlement metadata; ticker filters are not supported. Source: https://docs.kalshi.com/websockets/market-and-event-lifecycle
10. **Event discovery.** `GET /events` supports `series_ticker`, `status`, `with_nested_markets`, pagination, and up to 200 results per page. Source: https://docs.kalshi.com/api-reference/events/get-events
11. **Market snapshots.** `GET /markets/{ticker}` returns market status, timestamps, prices, strike fields, result, settlement value, settlement timestamp, rules, and other metadata. Source: https://docs.kalshi.com/api-reference/market/get-market
12. **Rate limits.** Authenticated requests use token-bucket read/write budgets; current endpoint costs are authoritative through account endpoint-cost APIs and 429 responses should be backed off. Source: https://docs.kalshi.com/getting_started/rate_limits
13. **API access is a supported product surface.** Kalshi’s current help center describes API access to account/order/trade/portfolio data and public market/order-book information. Trading eligibility and each contract remain subject to Kalshi account/market rules; re-check applicable terms before Phase 5. Sources: https://help.kalshi.com/en/articles/13823854-kalshi-api and https://help.kalshi.com/en/articles/13823822-market-rules

## ASSUMPTIONS

- Phase 1 data-integrity acceptance window is defined as **24 continuous hours and at least 90 KXBTC15M market windows**, whichever is longer. This is a project gate, not a Kalshi requirement.
- The collector treats any unexplained sequence gap or out-of-order event as gate-failing and reconnects fail-closed.
- Immutable JSONL is used for canonical raw ingress because it preserves exact source payloads cheaply; Parquet/DuckDB is reserved for derived research datasets in Phase 2.

## INFERENCES

- `sid`/`seq` should be treated as connection-scoped for validation. The project therefore records a generated `connection_id` and checks sequences within `(connection_id, sid)` rather than assuming global sequence identity.
- Lifecycle notifications are the best documented low-latency mechanism for detecting newly created KXBTC15M markets; the collector records them immediately and refreshes market subscriptions after a target lifecycle change.

## OPEN QUESTIONS / LIVE-VERIFICATION ITEMS

- Actual production entitlement and behavior of the authenticated BRTI WebSocket channel for the user's API key.
- Real production message rates, reconnect timing, and whether any undocumented transient ordering behavior appears at market rollovers.
- Exact operational latency from first lifecycle publication to fresh order-book subscription after reconnect.
- Phase 5 fee schedule, fee overrides, account tier, and endpoint costs must be re-verified immediately before any live execution implementation is enabled.
