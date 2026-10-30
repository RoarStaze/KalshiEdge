import math

import pytest

from kalshi_edge.bootstrap.v6_risk import NS, compute_brti_strike_risk


def _series():
    lock = 1_800_000_000 * NS
    values = [100 * math.exp(0.0001 * i) for i in range(121)]
    samples = [(lock - (120 - i) * NS, values[i]) for i in range(120)]
    return lock, samples, values[-1]


def test_causal_sigma_matches_known_log_returns():
    lock, samples, current = _series()
    result = compute_brti_strike_risk(
        samples, checkpoint_ts_ns=lock, brti_value=current,
        strike=100.0, seconds_remaining=240.0,
    )
    assert result is not None
    assert result.sigma_per_sqrt_second == pytest.approx(0.0001)
    assert result.observed_seconds == pytest.approx(120.0)
    assert result.observation_count == 121
    assert result.adjusted_distance == pytest.approx(
        abs(math.log(current / 100.0)) / (0.0001 * math.sqrt(200))
    )


def test_future_observation_never_changes_risk():
    lock, samples, current = _series()
    kwargs = dict(checkpoint_ts_ns=lock, brti_value=current, strike=100.0)
    baseline = compute_brti_strike_risk(samples, **kwargs)
    with_future = compute_brti_strike_risk(
        [*samples, (lock + NS, 1_000_000.0)], **kwargs
    )
    assert with_future == baseline


def test_missing_history_fails_closed():
    lock, samples, current = _series()
    assert compute_brti_strike_risk(
        samples[-12:], checkpoint_ts_ns=lock,
        brti_value=current, strike=100.0,
    ) is None


def test_ambiguous_duplicate_timestamp_fails_closed():
    lock, samples, current = _series()
    assert compute_brti_strike_risk(
        [*samples, samples[-1]], checkpoint_ts_ns=lock,
        brti_value=current, strike=100.0,
    ) is None


def test_zero_volatility_and_invalid_prices_fail_closed():
    lock, samples, current = _series()
    flat = [(ts, 100.0) for ts, _ in samples]
    assert compute_brti_strike_risk(
        flat, checkpoint_ts_ns=lock,
        brti_value=100.0, strike=99.0,
    ) is None
    assert compute_brti_strike_risk(
        samples, checkpoint_ts_ns=lock,
        brti_value=current, strike=0,
    ) is None


def test_farther_strike_has_larger_adjusted_distance():
    lock, samples, current = _series()
    def risk(strike):
        result = compute_brti_strike_risk(
            samples, checkpoint_ts_ns=lock,
            brti_value=current, strike=strike,
        )
        assert result is not None
        return result.adjusted_distance
    assert risk(95.0) > risk(100.0)
