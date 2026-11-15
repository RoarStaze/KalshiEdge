# Coverage-first deployment and 63% live confidence implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and verification-before-completion.

**Goal:** Reduce live forecast SKIPs and make future full-retrain promotions favor higher 63%-gate coverage with bounded accuracy loss.

**Architecture:** Central live constant controls final, AutoTrade and UI; versioned v3 full-retrain lockbox decision reuses v2 Kalshi/integrity safeguards but allows bounded incumbent degradation. Historical v2 reports and promoted bundles remain verifiable. Explicit local manual promotion of saved Model2 is separate from GitHub merge.

**Spec:** docs/superpowers/specs/2026-11-14-coverage-first-live63.md

## Tasks
- [ ] RED: test 63% final/AutoTrade boundary and SKIP reason, read-only dashboard, unchanged coinflip/integrity safety.
- [ ] RED: test coverage-first v3 successful slight accuracy decline, failures beyond 0.5pp qualified / 1.0pp overall, probabilities and ECE degraded, zero coverage gain, wrong gate, missing comparator; v2 history compatibility.
- [ ] Implement versioned promotion decision, next-run 0.63 gate scoring only for full retrain, keep v2 historical verification path and live loader compatibility.
- [ ] Change final gate/AutoTrade/UI to shared 63% constant; do not touch learned model artifacts or placement rules.
- [ ] Document exact local update, manual activation, SHA and new-threshold smoke checks. No trades from tests.
- [ ] CI full bootstrap/project/Docker; inspect PR diff; only merge if green.
