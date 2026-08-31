# Threat and Failure Model

## Phase 1 threats

- **Credential leakage:** keys stay outside the repository; no key material in logs.
- **Silent feed gaps:** sequence tracking detects discontinuities and forces reconnect.
- **Duplicate/out-of-order data:** detected by the dataset verifier and blocks the gate.
- **Corrupted raw history:** every finalized segment has a SHA-256 manifest.
- **Replay drift:** canonical JSONL and deterministic Decimal-based order-book reconstruction produce stable state hashes.
- **Clock ambiguity:** raw envelopes keep both source timestamps when available and local receive timestamps in nanoseconds.
- **Market rollover loss:** lifecycle events are recorded and force market rediscovery/resubscription.
- **REST/WS schema changes:** raw payloads are preserved verbatim with an envelope schema version; current docs must be re-verified before adapting parsers.

## Future execution red-team cases (Phase 4/5 gates)

Quote walking, spoof-like transient book changes, stale BRTI, dropped deltas, duplicate messages, latency spikes, partial fills, cancel/replace races, restart during exposure, and shadow-vs-real markout degradation must be simulated before live authorization.
