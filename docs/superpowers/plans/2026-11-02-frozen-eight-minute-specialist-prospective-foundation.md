# Frozen 480-second specialist shadow foundation — design and execution

**Goal.** Capture one immutable, development-only eight-minute specialist model for a future prospective, paired, read-only test against the current live predictor. No live confidence gate, AutoTrade rule, model default, capital exposure, or trading order changes.

## Alternatives and decision
- Refit the specialist every time evaluation is run: rejected; future data can change the candidate and contaminate a prospective test.
- Change `models/default.json` to the specialist: rejected; historical calibration and Kalshi-midpoint scores do not establish incumbent superiority.
- **Chosen:** create a separate, self-checking frozen JSON representation of the specialist's fitted standardization and logistic coefficients from the original verified *development* rows only. Use it later as a side-channel shadow, with a fresh timestamp boundary and the same 69% gate.

## Constraints
- Reuse `specialist_480.FEATURES`, the existing fixed `StandardScaler + LogisticRegression(C=.15)` and the causal `select_eight_minute_rows` selection, without tuning after inspecting previous validation.
- Validate the verified Parquet manifest/provenance *before* reading any historical feature input. Never materialize reserved lockbox rows or training data after the freeze.
- Fit only the prelockbox development partition. No calibration or lockbox *labels* are used for fitting.
- Serialize only numeric coefficients, means/scales and causal provenance to JSON (not pickle). Check the canonical SHA-256 payload on load. SHA is an integrity check, not an external signature.
- Freeze time strictly after last historical training observation and require later prospective observations to be after freeze time and contain one true ~480s checkpoint with valid source timestamps. Reject missing/stale/crossed features; no imputation.
- Create `data/bootstrap/research/480_specialist/frozen.json` exclusively; never overwrite a prior frozen model even if the dataset changes. Failure must not alter any production path.
- Keep the current deployed AI unmodified while collecting future shadow evidence. Scoring remains purely informational, not a trade instruction.

## Implementation plan
1. TDD: prove a deterministic frozen logistic prediction matches the fitted sklearn transform; check input chronology, freshness, feature count and 69%-gate interpretation; fail red first.
2. Implement numeric-only immutable freeze/load/score helpers plus fail-closed input validators.
3. Add read-only CLI `python -m kalshi_edge.bootstrap.frozen_specialist_480` that constructs the freeze from verified development rows only; if already frozen, verify the existing artifact and report it without overwriting.
4. Preserve and evaluate the 480s live checkpoint independently, without modifying the trading path. That integration is a separate, independently tested milestone, not implicitly turned on by this freeze CLI.
5. Verify full GitHub CI and inspect the output before proposing merge.

## What counts as evidence
- A frozen artifact is *not* an accuracy improvement. Later results must be timestamped after its creation; pair every eligible 480s specialist observation with the incumbent on the same market and official settlement. Count all confidence-based skips, model-unavailable skips and missing samples separately. Compare overall and qualifying accuracy, Brier/log loss, calibration, and fee-aware economics on prospective independent samples.
- A previously inspected retrospective calibration group and an archived Kalshi candle midpoint are not a fresh lockbox or executable ask quote.
