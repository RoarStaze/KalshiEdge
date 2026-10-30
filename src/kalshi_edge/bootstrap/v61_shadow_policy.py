from __future__ import annotations

"""Frozen V6.1 research-only confidence-shrinkage policy.

The z=2 scaling was chosen after looking at prior markets. The function is
NOT a statistically fitted or calibrated forecast. Evaluate only on NEW
settlements after the code is deployed; compare accuracy at matched coverage.
"""

import math

POLICY_ID = "v61-brti-risk-shrink-z2-v1"
REFERENCE_RISK_Z = 2.0


def risk_adjusted_shadow_probability(
    v6_yes: float | None,
    risk_ratio: float | None,
) -> float | None:
    """Conservatively shrink V6 toward 0.5 when causal BRTI risk is high.

    At risk_ratio >= 2, V6 is unchanged; at 0, probability is 0.5.
    Missing or nonfinite inputs -> None (unavailable, not an implicit SKIP
    or replacement of the V5/V6 forecast). The sign of p-.5 never flips.
    """
    if (
        isinstance(v6_yes, bool) or not isinstance(v6_yes, (float, int))
        or isinstance(risk_ratio, bool) or not isinstance(risk_ratio, (float, int))
    ):
        return None
    p = float(v6_yes)
    z = float(risk_ratio)
    if not math.isfinite(p) or not math.isfinite(z) or not (0.0 <= p <= 1.0) or z < 0:
        return None
    strength = min(1.0, z / REFERENCE_RISK_Z)
    return max(0.0, min(1.0, 0.5 + (p - 0.5) * strength))
