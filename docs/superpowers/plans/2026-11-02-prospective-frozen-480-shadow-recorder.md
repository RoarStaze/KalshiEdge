# Prospective frozen 480s shadow recorder plan — 2026-11-02

**Goal:** Record immutable, same-market future eight-minute forecasts from the existing incumbent and the already-frozen specialist without affecting live decisions or trading.

**Existing evidence:** 25,952 training markets and 2,900 already-inspected calibration markets. The local freeze hash is `17590f7f588398a347d076e18764aa9a6cf2b488bdf89917432235f5efe0191e`; this local artifact is NOT committed to GitHub. No prospective market has yet been scored.

**Decision:** Instrument the existing `bootstrap.live._run_live_async` after `persist_prediction_tick()` finishes the irreversible final forecast save. The recorder is optional (only enabled with a valid frozen artifact), read-only, exception-isolated, and exclusively writes one JSON record per ticker under the research directory. A failed shadow cannot modify or delay the finalized decision. Do not inspect settlements during inference.

**Data contract:** capture final ticker, scheduled and actual timestamp, strike, eight-minute market close, incumbent model hash/probability, frozen model hash and freeze timestamp, specialist probability/confidence gate or explicit skip reason, and a SHA-256 digest of canonical fields. Reject wrong ticker, incomplete/stale/crossed source features, mismatched tick times, input timestamps before freeze, and late checkpoints. Never overwrite an existing record. The existing `FinalForecastGate` keeps 69% as before.

**Excluded scope:** Weights are not refit; no change to model pointer, calibration, forecast threshold, UI, or AutoTrade; the specialist never submits exchange orders. A separate later analysis will pair these precommitted observations with official settlements and report matched accuracy, coverage, missing samples and score uncertainty.

## Test-first steps
1. Add tests of same-market, same-timestamp shadow record creation and fail-closed rows; confirm RED in CI before implementation.
2. Implement `shadow_live_480.py` frozen-model scoring and immutable record serialization; verify in CI.
3. Integrate optional frozen-load and shadow capture after finalized incumbent records, isolating research exceptions, without changing `persist_prediction_tick`.
4. Run the complete CI suite and container build; review exact diff. Only merge if all checks pass.

**Critical caveat:** Producing shadow records does not improve current live predictions. Test efficacy only on genuinely new official settlements after the recorded freeze time.
