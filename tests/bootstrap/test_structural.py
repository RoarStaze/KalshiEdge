from __future__ import annotations

import importlib.util
import json

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


def _validation_rows() -> tuple[FeatureRow, ...]:
    rows: list[FeatureRow] = []
    base = 1_900_000_000_000_000_000
    for index, (ret, label) in enumerate(((-0.0008, 0), (-0.0002, 0), (0.0002, 1), (0.0008, 1))):
        rows.append(
            FeatureRow(
                market_ticker=f"KXBTC15M-VALID-{index}",
                market_date="2026-08-01",
                split_group_id=f"KXBTC15M-VALID-{index}",
                checkpoint_ts_ns=base + index * 900 * NS,
                label_yes=label,
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
    rows.append(
        FeatureRow(
            market_ticker="KXBTC15M-VALID-SUBMINUTE",
            market_date="2026-08-01",
            split_group_id="KXBTC15M-VALID-SUBMINUTE",
            checkpoint_ts_ns=base + 4 * 900 * NS,
            label_yes=1,
            features={
                "seconds_remaining": 45.0,
                "strike": 100_000.0,
                "btc_close": 100_020.0,
                "btc_return_5s": 0.0002,
                "btc_return_5s_available": 1.0,
                "btc_realized_vol_60s": 0.0008,
            },
            source_max_ts_ns={"binance": base + 4 * 900 * NS},
        )
    )
    return tuple(rows)


def _model(candidate: str = "diffusion"):
    return structural.StructuralModel.fit(
        _training_rows(),
        candidate=candidate,
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


@pytest.mark.parametrize("candidate", ["diffusion", "empirical_residual"])
def test_seeded_structural_probability_is_bounded_reproducible_and_monotone(candidate: str) -> None:
    model = _model(candidate)
    low = structural.StructuralState(
        current_value=99_900.0,
        strike=100_000.0,
        seconds_remaining=120.0,
        volatility_per_second=0.0008,
        recent_return_5s=0.0,
    )
    high = low.model_copy(update={"current_value": 100_100.0})

    p_low_first = model.predict_proba(low)
    p_low_second = model.predict_proba(low)
    p_high = model.predict_proba(high)

    assert 0.0 <= p_low_first <= 1.0
    assert p_low_first == p_low_second
    assert 0.0 <= p_high <= 1.0
    assert p_high >= p_low_first


def test_pre_final_prediction_requires_exact_final_minute_state_once_window_has_started() -> None:
    state = structural.StructuralState(
        current_value=100_000.0,
        strike=100_000.0,
        seconds_remaining=59.0,
        volatility_per_second=0.0008,
        recent_return_5s=0.0,
    )

    with pytest.raises(structural.StructuralDataError, match="final-minute state"):
        _model().predict_proba(state)


@pytest.mark.parametrize("candidate", ["diffusion", "empirical_residual"])
def test_partial_final_minute_probability_is_bounded_and_reproducible(candidate: str) -> None:
    model = _model(candidate)
    state = structural.FinalMinuteState(
        strike=100_000.0,
        current_value=100_020.0,
        volatility_per_second=0.0008,
        elapsed_observations=30,
        observations=tuple(_observation(index, 100_000.0 + index) for index in range(30)),
    )

    first = model.predict_final_minute(state)
    second = model.predict_final_minute(state)

    assert 0.0 <= first <= 1.0
    assert first == second


def _metrics(candidate: str, log_loss: float, brier: float, calibration: float):
    return structural.StructuralCandidateMetrics(
        candidate=candidate,
        log_loss=log_loss,
        brier_score=brier,
        calibration_error=calibration,
        row_count=10,
    )


def test_candidate_choice_orders_log_loss_then_brier_then_calibration() -> None:
    assert structural.choose_structural_candidate(
        (_metrics("diffusion", 0.20, 0.20, 0.20), _metrics("empirical_residual", 0.21, 0.01, 0.01))
    ) == "diffusion"
    assert structural.choose_structural_candidate(
        (_metrics("diffusion", 0.20, 0.10, 0.20), _metrics("empirical_residual", 0.20, 0.09, 0.30))
    ) == "empirical_residual"
    assert structural.choose_structural_candidate(
        (_metrics("diffusion", 0.20, 0.10, 0.05), _metrics("empirical_residual", 0.20, 0.10, 0.04))
    ) == "empirical_residual"


def test_chronological_selector_evaluates_only_pre_final_rows_and_saves_evidence(tmp_path) -> None:
    selection = structural.select_structural_model(
        _training_rows(),
        _validation_rows(),
        seed=73115,
        simulations=128,
    )

    assert selection.model.candidate == selection.evidence.chosen_candidate
    assert selection.evidence.evaluated_rows == 4
    assert selection.evidence.excluded_subminute_rows == 1
    assert selection.evidence.training_end_ts_ns < selection.evidence.validation_start_ts_ns
    assert {metric.candidate for metric in selection.evidence.metrics} == {"diffusion", "empirical_residual"}

    evidence_path = tmp_path / "structural-selection.json"
    structural.save_selection_evidence(selection.evidence, evidence_path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["chosen_candidate"] == selection.evidence.chosen_candidate
    assert payload["evaluated_rows"] == 4


def test_chronological_selector_rejects_split_group_overlap() -> None:
    validation = list(_validation_rows())
    validation[0] = validation[0].model_copy(update={"split_group_id": _training_rows()[0].split_group_id})

    with pytest.raises(structural.StructuralDataError, match="split group overlap"):
        structural.select_structural_model(_training_rows(), validation, simulations=64)


def test_chronological_selector_rejects_nonchronological_validation() -> None:
    validation = list(_validation_rows())
    validation[0] = validation[0].model_copy(
        update={"checkpoint_ts_ns": _training_rows()[0].checkpoint_ts_ns - NS}
    )

    with pytest.raises(structural.StructuralDataError, match="strictly after"):
        structural.select_structural_model(_training_rows(), validation, simulations=64)
