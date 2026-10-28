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



def test_missing_60s_realized_volatility_uses_observed_300s_not_an_imputed_value() -> None:
    row = _row(1, 1)
    inputs = {**row.features, "btc_realized_vol_60s": 0.0}
    long_only = volatility.analytic_estimate(inputs)
    assert long_only is not None
    assert long_only.volatility_per_second == pytest.approx(inputs["btc_realized_vol_300s"])
    short_only = volatility.analytic_estimate({
        **inputs, "btc_realized_vol_60s": .0002,
        "btc_realized_vol_300s": float("nan"),
    })
    assert short_only is not None
    assert short_only.volatility_per_second == pytest.approx(.0002)
    # Both windows absent: do not invent a sigma or make a confident guess.
    assert volatility.analytic_estimate({
        **inputs, "btc_realized_vol_300s": 0.0,
    }) is None


def test_historical_missing_volatility_is_not_a_fatal_error_or_dropped_from_scoring() -> None:
    rows = [_row(i, i % 2) for i in range(120)]
    # 15 early and 5 later rows have no real observed volatility.
    missing_indices = {*range(0, 15), *range(90, 95)}
    incomplete = [
        row.model_copy(update={"features": {
            **row.features,
            "btc_realized_vol_60s": 0.0,
            "btc_realized_vol_300s": 0.0,
        }}) if i in missing_indices else row
        for i, row in enumerate(rows)
    ]
    baseline = [.25 if row.label_yes else .75 for row in incomplete]
    selection = volatility.select_volatility_overlay(incomplete, baseline)
    assert selection.fit_markets == 80
    assert selection.validation_markets == 40
    assert selection.usable_fit_markets == 65
    assert selection.usable_validation_markets == 35
    assert selection.unavailable_markets == 20
    assert selection.accepted
    assert selection.overlay is not None
    # The actual held-out Brier score includes ALL 40 calibration examples,
    # including the five missing-volatility examples left at incumbent p.
    validation = incomplete[80:]
    adjusted = [
        selection.overlay.predict(base, row.features)
        for row, base in zip(validation, baseline[80:])
    ]
    assert selection.validation_challenger.brier == pytest.approx(
        volatility.probability_metrics(
            [row.label_yes for row in validation], adjusted,
        ).brier
    )
    assert adjusted[10] == baseline[90]


def test_inadequate_observed_calibration_is_diagnosed_before_lockbox() -> None:
    rows = [_row(i, i % 2) for i in range(120)]
    # Starve the chronological validation block, even though 80 early markets
    # have good observed volatility.
    incomplete = [
        row.model_copy(update={"features": {
            **row.features,
            "btc_realized_vol_60s": 0.0,
            "btc_realized_vol_300s": 0.0,
        }}) if i >= 80 else row
        for i, row in enumerate(rows)
    ]
    with pytest.raises(
        volatility.VolatilityChallengerError,
        match=r"usable=80/120, fit=80/80, validation=0/40",
    ):
        volatility.select_volatility_overlay(incomplete, [.5] * 120)
