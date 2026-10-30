"""Live-level integration tests for read-only V6.1 BRTI risk diagnostics."""

import math

import pytest

from kalshi_edge.bootstrap.live_kalshi import BRTIObservation
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


def test_dense_brti_history_records_risk_without_changing_v5_or_v6():
    now = CLOSE - 240 * NS
    dense = _dense_state(now)
    sparse = _healthy_state(now)
    sparse.brti_observations.clear()
    sparse.latest_brti_ts_ns = None
    sparse.update_brti(dense.latest_brti)

    from_dense = _predictor(dense).predict(now)
    from_sparse = _predictor(sparse).predict(now)

    assert from_dense.status == "OK"
    assert from_sparse.status == "OK"
    assert from_dense.p_yes == pytest.approx(from_sparse.p_yes)
    assert from_dense.brti_shadow_probability == from_sparse.brti_shadow_probability
    assert from_dense.brti_shadow_risk_ratio is not None
    assert from_dense.brti_shadow_risk_ratio > 0
    assert from_dense.brti_shadow_risk_sigma is not None
    assert from_dense.brti_shadow_risk_sigma > 0
    assert from_dense.brti_shadow_risk_observed_seconds >= 60
    assert from_dense.brti_shadow_risk_observation_count >= 31
    assert from_sparse.brti_shadow_risk_ratio is None


def test_future_brti_observation_is_excluded_from_risk():
    now = CLOSE - 240 * NS
    state = _dense_state(now)
    expected = _predictor(state).predict(now)
    # Artificial, deliberately out-of-order future record inserted into
    # retained history, not the latest live BRTI value.
    state.brti_observations.insert(0, BRTIObservation(
        source_ts_ns=now + NS, value=90_000.0,
    ))
    actual = _predictor(state).predict(now)

    assert actual.status == "OK"
    assert actual.brti_shadow_risk_ratio == pytest.approx(expected.brti_shadow_risk_ratio)
    assert actual.p_yes == pytest.approx(expected.p_yes)
    assert actual.brti_shadow_probability == expected.brti_shadow_probability
