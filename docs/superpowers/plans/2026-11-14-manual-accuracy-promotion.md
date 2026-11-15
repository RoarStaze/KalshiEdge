# Implementation plan: deliberate manual acceptance of rejected model

> **Superpowers:** test-driven-development; verification-before-completion; no source modifications before failing tests.

1. Add RED tests for read-only preview of current completed rejected experiment, exact hash requirement, lossless backed-up pointer and explicit nonautomatic provenance, rollback, forbidden integrity/cause codes, mismatched/tampered saved report, no re-scoring, stable live loader.
2. Add `manual_promotion.py` CLI: `preview`, `activate --expected-experiment-sha ... --accept-manual-risk`, `rollback --expected-active-hash ... --accept-manual-risk`. Use existing `artifact`, `evaluate`, `model_identity` and `promotion_audit` verification/atomic file primitives.
3. Update `live.load_default_bundle` allowed rule list to the new explicit, auditable identifier only with original reason codes.
4. Document commands and hazards. Include no code changing AutoTrade or loosened automatic rules.
5. Verify CI bootstrap/full/Docker, diff review; merge only green PR. Do not touch current local worker/install.
