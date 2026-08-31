from __future__ import annotations

import importlib.util

import pytest

from kalshi_edge.bootstrap import structural
from kalshi_edge.bootstrap.types import FeatureRow


NS = 1_000_000_000


def test_structural_module_exists() -> None:
    assert importlib.util.find_spec("kalshi_edge.bootstrap.structural") is not None


def _training_rows() -> tuple[FeatureRow, ...]:
    rows: list[FeatureRow] = []
    base = 1_800_000_000_000_000_000
    for index, ret in enumerate((-0.0010, -0.0005, 0.0, 0.0005, 0.0010, 0.00025, -0.00025, 0.00075)):
        rows.append(
            FeatureRow(
                market_ticker=f"KXBTC15M-TRAIN-{index}",
                market_date="2026-07-01",
                split_group_id=f"KXBTC15M-TRAIN-{index}",
                checkpoint_ts_ns=base + index * 900 * NS,
                label_yes=1 if ret >= 0 else 0,
                features={
                    "seconds_remaining": 120.0,
                    "strike": 100_000.0,
                    "btc_close": 100_000.0 * (1.0 + ret),
                    "btc_return_5s": ret,
                    "btc_return_5s_available": 1.0,
                    "btc_realized_vol_60s": 0.0008,
                },
                source_max_ts_ns={"binance": base + index * 900 * NS},
            )
        )
    return tuple(rows)


def _model():
    return structural.StructuralModel.fit(
        _training_rows(),
        candidate="diffusion",
        seed=73115,
        simulations=256,
    )


def _observation(second_index: int, value: float, *, ambiguous: bool = False):
    return structural.FinalMinuteObservation(
        second_index=second_index,
        value=value,
        source_ts_ns=1_900_000_000_000_000_000 + second_index * NS,
        ambiguous=ambiguous,
    )


def test_required_remaining_mean_equals_strike_when_first_half_averages_strike() -> None:
    assert structural.required_remaining_mean(
        strike=100_000.0,
        observed_values=[100_000.0] * 30,
    ) == pytest.approx(100_000.0)


def test_required_remaining_mean_rejects_complete_window() -> None:
    with pytest.raises(structural.StructuralDataError, match="no remaining"):
        structural.required_remaining_mean(
            strike=100_000.0,
            observed_values=[100_000.0] * 60,
        )


def test_complete_final_minute_collapses_to_verified_at_least_rule() -> None:
    model = _model()
    equal_state = structural.FinalMinuteState(
        strike=100_000.0,
        current_value=100_000.0,
        volatility_per_second=0.0008,
        elapsed_observations=60,
        observations=tuple(_observation(index, 100_000.0) for index in range(60)),
    )
    below_state = equal_state.model_copy(
        update={
            "current_value": 99_999.99,
            "observations": tuple(_observation(index, 99_999.99) for index in range(60)),
        }
    )

    assert model.predict_final_minute(equal_state) == 1.0
    assert model.predict_final_minute(below_state) == 0.0


@pytest.mark.parametrize(
    ("elapsed", "sample_specs", "match"),
    [
        (2, ((0, 100_000.0, False), (0, 100_001.0, False)), "duplicate"),
        (3, ((0, 100_000.0, False), (2, 100_001.0, False)), "missing"),
        (2, ((0, 100_000.0, False), (1, 100_001.0, True)), "ambiguous"),
    ],
)
def test_final_minute_bad_sample_quality_fails_closed(elapsed, sample_specs, match: str) -> None:
    observations = tuple(
        _observation(second_index, value, ambiguous=ambiguous)
        for second_index, value, ambiguous in sample_specs
    )
    state = structural.FinalMinuteState(
        strike=100_000.0,
        current_value=100_001.0,
        volatility_per_second=0.0008,
        elapsed_observations=elapsed,
        observations=observations,
    )

    with pytest.raises(structural.StructuralDataError, match=match):
        _model().predict_final_minute(state)
