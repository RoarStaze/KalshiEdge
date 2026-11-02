# Fixed 480-second confidence-rescue shadow policy — 2026-11-02

## Goal and constraints

Goal: fewer final forecast SKIPs **while increasing correct qualifying forecasts per settled market**, without falsely claiming a proven accuracy advantage from 3 paired outcomes. Preserve the already deployed model and AutoTrade. The frozen specialist SHA must stay unchanged.

## Policy alternatives

1. **Promote specialist now:** rejects historical gate on 3 examples; not justified.
2. **Lower the 69% threshold:** more forecasts but unmeasured reduction in reliability.
3. **Research-only selective rescue of incumbent low-confidence SKIPs (chosen):** preserve original YES/NO decisions. Only when incumbent specifically skipped due to `CONFIDENCE_BELOW_69_PERCENT` at the valid eight-minute lock, and an exact matched frozen, causal shadow snapshot already exists with `status=OK` and fixed gate qualification, hypothetically take the frozen side. Never rescue missing feeds, missed deadlines, corrupt snapshots, or absent Kalshi settlements.

## Evidence and evaluation

- Score using *only pre-settlement persisted* original finals + frozen shadow JSON and *later verified* official settlements. `_load_finals`, `_validated_shadow`, `_load_saved_settlement` handle integrity.
- Two denominators: all settled, after-freeze incumbent finals (conservative including missing shadows); and valid shadow observations only (paired observability, missingness risk explicitly disclosed).
- Report frozen-and-incumbent identity hashes, count candidate rescue opportunities, actual correctly-rescued and incorrectly-rescued markets, original qualified/correct/coverage/accuracy, hybrid counterfactual qualified/correct/coverage/accuracy and correct-forecast improvement, clear reporting on unobservable/missing.
- Freeze the existing `0.69` threshold; no tuning, no other features. Include one-sided Wilson 95% lower accuracy bound as uncertainty annotation; sample minimum remains descriptive only, never triggers model promotion.
- No trading, model fit, new model training, calibration, UI, live `FinalForecastGate` or `AutoTrade` wiring. CLI is read-only.

## Implementation steps

1. Create tests first for exactly matched, after-freeze, valid persisted low-confidence SKIP rescued by specialist, preserving incumbent qualified decisions.
2. Test missing/invalid shadow, explicit specialist skip, non-confidence incumbents SKIPs, pending and invalid official settlements, and exact immutable model identity.
3. Observe CI failure due to missing module.
4. Implement separate `bootstrap/rescue_policy_480.py` with pure decision function and read-only report CLI using existing verified data. Fail closed, no file writes.
5. Verify full tests and container build, diff review; PR+merge only with successful CI.
