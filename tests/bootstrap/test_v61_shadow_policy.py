"""Pure-policy tests: no repo, network, model fitting, or lockbox access."""
import math
import pytest
from kalshi_edge.bootstrap.v61_shadow_policy import (
    POLICY_ID, risk_adjusted_shadow_probability,
)


def test_freeze_identifier_is_explicit():
    assert POLICY_ID == "v61-brti-risk-shrink-z2-v1"


def test_high_risk_ratio_identity():
    assert risk_adjusted_shadow_probability(0.95, 2.0) == pytest.approx(0.95)
    assert risk_adjusted_shadow_probability(0.05, 20.0) == pytest.approx(0.05)


def test_middle_risk_shrinks_confidence_not_direction():
    assert risk_adjusted_shadow_probability(0.9, 1.0) == pytest.approx(0.7)
    assert risk_adjusted_shadow_probability(0.1, 1.0) == pytest.approx(0.3)


def test_zero_risk_no_confident_prediction():
    assert risk_adjusted_shadow_probability(0.99, 0.0) == 0.5
    assert risk_adjusted_shadow_probability(0.01, 0.0) == 0.5


def test_missing_risk_does_not_fake_confidence():
    assert risk_adjusted_shadow_probability(0.9, None) is None
    assert risk_adjusted_shadow_probability(None, 2.0) is None


def test_invalid_inputs_fail_closed():
    for prob, risk in [(-.1, 2), (1.1, 2), (.9, -1), (.9, math.nan),
                       (.9, math.inf), (math.nan, 2), (True, 2), (.9, True),
                       ("0.9", 2), (.9, "2")]:
        assert risk_adjusted_shadow_probability(prob, risk) is None
