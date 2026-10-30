"""V6.1 research fields must not affect V5/V6 or the immutable final gate."""
import math
import pytest

from kalshi_edge.bootstrap.live_kalshi import BRTIObservation
from kalshi_edge.bootstrap.v61_shadow_policy import POLICY_ID, risk_adjusted_shadow_probability
from bootstrap.test_live import CLOSE, NS, _healthy_state, _predictor


def _dense_state(now):
    state = _healthy_state(now)
    state.brti_observations.clear()
    state.latest_brti_ts_ns = None
    for i in range(120):
        state.update_brti(BRTIObservation(
            source_ts_ns=now - (120 - i) * NS,
            value=60_001.0 * math.exp(0.00001 * i),
        ))
    return state


def test_research_policy_only_adds_shadow_fields():
    now = CLOSE - 240 * NS
    dense = _dense_state(now)
    sparse = _healthy_state(now)
    sparse.brti_observations.clear()
    sparse.latest_brti_ts_ns = None
    sparse.update_brti(dense.latest_brti)
    d = _predictor(dense).predict(now)
    s = _predictor(sparse).predict(now)
    assert d.status == s.status == "OK"
    assert d.p_yes == pytest.approx(s.p_yes)
    assert d.confidence == pytest.approx(s.confidence)
    assert d.brti_shadow_probability == s.brti_shadow_probability
    assert d.v61_shadow_probability == pytest.approx(risk_adjusted_shadow_probability(
        d.brti_shadow_probability, d.brti_shadow_risk_ratio))
    assert d.v61_shadow_policy == POLICY_ID
    assert s.v61_shadow_probability is None
    assert s.v61_shadow_policy is None


def test_unavailable_future_observation_does_not_affect_new_shadow():
    now = CLOSE - 240 * NS
    state = _dense_state(now)
    before = _predictor(state).predict(now)
    state.brti_observations.insert(0, BRTIObservation(
        source_ts_ns=now + NS, value=90_000.0))
    after = _predictor(state).predict(now)
    assert after.status == "OK"
    assert after.p_yes == pytest.approx(before.p_yes)
    assert after.brti_shadow_probability == before.brti_shadow_probability
    assert after.v61_shadow_probability == pytest.approx(before.v61_shadow_probability)
