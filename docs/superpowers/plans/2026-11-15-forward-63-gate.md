# Forward-trainer 63% correction plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and verification-before-completion.

**Goal:** Make new research 480s training use the shared live 63% confidence threshold and label old/new reports accurately.

**Tests:** RED tests for new candidate+incumbent 63%-gate keys, equal-threshold scoring vs historical issuance, correct display and new 63% SKIP capture; then GREEN CI across entire project/Docker.

**Implementation:** Patch `forward_learning_480`, `training_cycle` forward reason reader, `forward_feature_evidence_480` skip categorization, forward tests and docs. Preserve immutable historical report compatibility. No model promotion, retraining logic or trading changes.

**Merge:** GitHub branch → independent PR CI → merge main → PowerShell git pull.
