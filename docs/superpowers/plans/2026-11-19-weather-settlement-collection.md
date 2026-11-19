# Weather first-party settlement reconciliation — 2026-11-19

**Goal:** Automatically retrieve primary Weather Company daily-high reports and independent Kalshi settled contracts, retain verifiable raw evidence with receipt timestamps, and admit only strictly cross-checked labels into WeatherEdge prospective calibration. Continue running under `kalshiedge start`.

**Source research:** The Weather Company publishes climate values at https://weather.com/kalshi, with a reported JSON data endpoint `/kalshi/api/climate/primary?date=YYYY-MM-DD` documented by independent observers but **not a stable vendor API contract**. Kalshi's documented public `GET /markets?event_ticker=...` and historical markets endpoints expose market-specific `rules_primary`, `result`, and `settlement_ts`. The Weather Company and Kalshi general help have differing statements; actual market `rules_primary` takes priority. No substitute METAR can be promoted to settlement evidence.

**Architecture:** `settlement_collector.py` implements a conservative primary-source adapter, crosschecks against Kalshi results, saves first-party raw snapshots with checksums, and maintains an atomic machine-evidence manifest. The existing `calibration.py` validates machine evidence by re-parsing *both* captured first-party payloads before including in paired metrics. The existing single WeatherSupervisor process polls once after local climate day end and periodically for revisions, but never places orders.

**Security constraints:** No API secrets, no arbitrary URLs, no signed-user approvals bypassed, HTTPS host pinned with redirect disabled, no loose source string matching, no user-supplied model outputs promoted. Publisher payload shape unresolved => explicit `UNSUPPORTED_PUBLISHER_SCHEMA` and no label, rather than claiming verified data. Rate limited, bounded lookback and capture sizes, atomic writes, revision-safe idempotency. Certificate status is never made true by code.

**Tests first:** HTTPX MockTransport for incomplete preliminary source, wrong station/day, per-contract publisher mismatch, market settlement mismatch, exact bracket parity, dual-source hash persistence, after-day cutoff, revision handling and no duplicate label; test existing supervisor runs without a separate CLI. Exercise dashboard visible automatic reconciliation state.

**Tasks:**
- [ ] Test failure gates and machine evidence reconciliation functions before writing implementation.
- [ ] Add bounded network source fetch and conservative schema adapter; no fake TWC response assumptions.
- [ ] Strictly match Kalshi contract station/date/source/settled strikes against TWC published daily high.
- [ ] Preserve versioned raw HTTP payloads, hashes and latest automatically reconciled evidence atomically.
- [ ] Update calibration audit to independently verify raw first-party snapshots and score only accepted evidence; no relaxed acceptance of hand-written values.
- [ ] Register poller in existing WeatherSupervisor / health / Weather UI. Single-command startup remains.
- [ ] Validate full GitHub Actions CI, review diff, squash PR to main only on green. No claim of measured real-world performance until genuine publisher bytes have been retrieved.
