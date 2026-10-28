from __future__ import annotations

import math
from pathlib import Path

import pytest

from kalshi_edge.bootstrap import volatility
from kalshi_edge.bootstrap.types import FeatureRow


def _row(i: int, label: int, *, remaining: float = 240.0, rv: float = .00035) -> FeatureRow:
    return FeatureRow(
        market_ticker=f"KXBTC15M-VOL-{i:04d}",
        market_date="2026-10-28",
        split_group_id=f"KXBTC15M-VOL-{i:04d}",
        checkpoint_ts_ns=1_800_000_000_000_000_000 + i * 900_000_000_000,
        label_yes=label,
        features={
            "btc_close": 100.5 if label else 99.5,
            "strike": 100.0,
            "seconds_remaining": remaining,
            "btc_realized_vol_60s": rv,
            "btc_realized_vol_300s": rv,
        },
        source_max_ts_ns={"binance": 1_800_000_000_000_000_000 + i * 900_000_000_000},
    )


def test_more_volatility_reduces_overconfident_strike_distance() -> None:
    fixed = _row(1, 1, rv=.00015).features
    turbulent = _row(1, 1, rv=.001).features
    calm = volatility.analytic_estimate(fixed)
    noisy = volatility.analytic_estimate(turbulent)
    assert calm is not None and noisy is not None
    assert calm.probability_above > noisy.probability_above > .5
    assert calm.expected_move_pct < noisy.expected_move_pct
    assert calm.effective_seconds == 200.0


def test_direction_reversal_and_invalid_volatility_are_fail_closed() -> None:
    above = volatility.analytic_estimate(_row(1, 1).features)
    below = volatility.analytic_estimate(_row(1, 0).features)
    assert above is not None and below is not None
    assert above.probability_above > .5
    assert below.probability_above < .5

    for fields in (
        {"btc_realized_vol_60s": 0.0},
        {"btc_realized_vol_60s": float("nan")},
        {"seconds_remaining": 59.0},
        {"btc_close": 0.0},
    ):
        inputs = {**_row(1, 1).features, **fields}
        assert volatility.analytic_estimate(inputs) is None
    assert volatility.VolatilityOverlay(.2).predict(.62, {"strike": 100}) == .62
    with pytest.raises(volatility.VolatilityChallengerError):
        volatility.VolatilityOverlay(0.0)


def test_blend_never_exceeds_bounded_probability_or_changes_unavailable_rows() -> None:
    overlay = volatility.VolatilityOverlay(.2)
    features = _row(1, 1).features
    estimate = volatility.analytic_estimate(features)
    assert estimate is not None
    blended = overlay.predict(.70, features)
    assert blended == pytest.approx(.8 * .70 + .2 * estimate.probability_above)
    assert 0.0 <= blended <= 1.0
    assert overlay.predict(.70, {**features, "seconds_remaining": 20.0}) == .70


def test_chronological_calibration_selects_improving_volatility_candidate() -> None:
    rows = [_row(i, i % 2) for i in range(120)]
    # The incumbent is consistently biased against the informative price
    # distance feature; the held-out volatility overlay should correct it.
    baseline = [.25 if row.label_yes else .75 for row in rows]
    selection = volatility.select_volatility_overlay(rows, baseline)
    assert selection.accepted
    assert selection.overlay is not None
    assert selection.selected_weight in volatility.WEIGHTS[1:]
    assert selection.validation_challenger.brier < selection.validation_baseline.brier
    assert selection.validation_challenger.log_loss < selection.validation_baseline.log_loss
    assert selection.fit_markets == 80
    assert selection.validation_markets == 40


def test_noise_that_does_not_improve_rejects_before_lockbox() -> None:
    rows = [_row(i, i % 2) for i in range(120)]
    # The incumbent is already decisively correct on each observation.
    baseline = [.999 if row.label_yes else .001 for row in rows]
    selection = volatility.select_volatility_overlay(rows, baseline)
    assert not selection.accepted
    assert selection.overlay is None
    assert selection.selected_weight == 0.0


def test_duplicate_market_and_non_lock_checkpoint_rejected() -> None:
    rows = [_row(i, i % 2) for i in range(120)]
    with pytest.raises(volatility.VolatilityChallengerError, match="one final"):
        volatility.select_volatility_overlay([rows[0], *rows[0:119]], [0.5] * 120)
    with pytest.raises(volatility.VolatilityChallengerError, match="non-final"):
        volatility.select_volatility_overlay([_row(0, 1, remaining=300), *rows[1:]], [0.5] * 120)
