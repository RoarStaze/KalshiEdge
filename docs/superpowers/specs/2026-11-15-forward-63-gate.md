# Correct forward training confidence gate to 63%

User showed a completed 480s research training screen still saying 69% despite Model #2 live threshold 63%. Root cause verified: `forward_learning_480.CONFIDENCE_GATE = 0.69` is separate from `final_forecast.MINIMUM_FINAL_CONFIDENCE=0.63`. Both `forward_learning_480.evaluate_forward_candidate` and `training_cycle._forward_failure_reasons` expose 69-only report keys; `training_cycle` UI renders these keys with a 69 label. This is a mismatch, not a live model failure.

**Change:** use the shared live minimum 0.63 for the *new* 480s research comparison. Score **both** challenger and saved incumbent probabilities at the **same** threshold with the same observed coinflip veto, rather than using saved issuance flags from older 69% policy. New report fields `candidate_gate`, `incumbent_gate`, `gate_confidence=0.63`, and new historical research report ID. Historical archived research reports with true `candidate_69_gate` remain readable and labelled 69%. The forward path remains offline/research-only, distinct from actual incremental fine-tuning.

Also update forward evidence counterfactual confidence-skip statistics to accept historical 69% reason and new 63% reason. No edits to actual model, AutoTrade, threshold, training data or trading actions.
