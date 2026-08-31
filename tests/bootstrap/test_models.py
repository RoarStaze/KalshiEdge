from __future__ import annotations

import math

import pytest

from kalshi_edge.bootstrap import models, splits
from kalshi_edge.bootstrap.types import FeatureRow


NS = 1_000_000_000


def _rows(count: int = 14) -> tuple[FeatureRow, ...]:
    base = 1_800_000_000_000_000_000
    rows: list[FeatureRow] = []
    for index in range(count):
        label = index % 2
        signal = -1.0 if label == 0 else 1.0
        rows.append(
            FeatureRow(
                market_ticker=f"KXBTC15M-{index:02d}",
                market_date=f"2026-07-{index + 1:02d}",
                split_group_id=f"KXBTC15M-{index:02d}",
                checkpoint_ts_ns=base + index * 900 * NS,
                label_yes=label,
                features={
                    "signal": signal + (index * 0.01),
                    "distance_bps": signal * 10.0,
                    "kalshi_prior": 0.2 if label == 0 else 0.8,
                },
                source_max_ts_ns={"binance": base + index * 900 * NS},
            )
        )
    return tuple(rows)


def _folds(rows: tuple[FeatureRow, ...]):
    markets = tuple(
        splits.MarketIndex(
            market_ticker=row.market_ticker,
            split_group_id=row.split_group_id or row.market_ticker,
            first_checkpoint_ts_ns=row.checkpoint_ts_ns,
        )
        for row in rows
    )
    return splits.make_walk_forward_splits(
        markets,
        min_train_markets=4,
        validation_markets=2,
        embargo_markets=1,
    )


def _logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))


def test_candidate_training_is_seeded_and_never_scores_lockbox() -> None:
    rows = _rows()
    folds = _folds(rows)

    first = models.train_candidate_models(rows, folds, seed=73115)
    second = models.train_candidate_models(rows, folds, seed=73115)

    assert first.candidate_names == ("logistic", "hist_gradient_boosting", "xgboost")
    assert first.best_candidate in first.candidate_names
    assert first.oof_predictions == second.oof_predictions
    assert first.selected_params == second.selected_params

    validation_indices = set().union(*(set(fold.validation_indices) for fold in folds))
    lockbox_indices = set(folds[0].lockbox_indices)
    for candidate in first.candidate_names:
        predictions = first.oof_predictions[candidate]
        assert len(predictions) == len(rows)
        for index in validation_indices:
            assert predictions[index] is not None
            assert 0.0 <= predictions[index] <= 1.0
        for index in lockbox_indices:
            assert predictions[index] is None


def test_residual_model_is_zero_weight_when_it_fails_to_beat_kalshi_prior() -> None:
    train_x = [[0.0], [0.0], [0.0], [0.0], [0.0], [0.0]]
    train_y = [0, 1, 0, 1, 0, 1]
    train_prior = [0.4, 0.6, 0.4, 0.6, 0.4, 0.6]
    validation_x = [[0.0], [0.0], [0.0], [0.0]]
    validation_y = [0, 1, 0, 1]
    validation_prior = [0.01, 0.99, 0.01, 0.99]

    fitted = models.fit_residual_model(
        train_x,
        train_y,
        train_prior,
        validation_x,
        validation_y,
        validation_prior,
        seed=73115,
    )

    assert fitted.component_weight == 0.0
    assert fitted.predict(validation_x, validation_prior) == pytest.approx(validation_prior)


def test_residual_model_uses_kalshi_logit_as_fixed_unit_offset() -> None:
    train_x = [[-1.0], [-0.7], [-0.4], [0.4], [0.7], [1.0]]
    train_y = [0, 0, 0, 1, 1, 1]
    train_prior = [0.5] * 6
    validation_x = [[-0.8], [-0.5], [0.5], [0.8]]
    validation_y = [0, 0, 1, 1]
    validation_prior = [0.5] * 4

    fitted = models.fit_residual_model(
        train_x,
        train_y,
        train_prior,
        validation_x,
        validation_y,
        validation_prior,
        seed=73115,
    )

    assert fitted.component_weight == 1.0
    low, high = fitted.predict([[0.0], [0.0]], [0.2, 0.8])
    corrected_logit_gap = _logit(high) - _logit(low)
    prior_logit_gap = _logit(0.8) - _logit(0.2)
    assert corrected_logit_gap == pytest.approx(prior_logit_gap, rel=1e-7, abs=1e-7)


def test_simplex_stacker_has_nonnegative_unit_sum_weights_and_favors_better_component() -> None:
    labels = [0, 1, 0, 1, 0, 1, 0, 1]
    component_predictions = {
        "kalshi": [0.1, 0.9, 0.15, 0.85, 0.2, 0.8, 0.1, 0.9],
        "structural": [0.45, 0.55, 0.45, 0.55, 0.45, 0.55, 0.45, 0.55],
        "historical_ml": [0.3, 0.7, 0.35, 0.65, 0.3, 0.7, 0.35, 0.65],
    }

    stacker = models.fit_stacker(component_predictions, labels)
    predictions = stacker.predict(component_predictions)

    assert set(stacker.weights) == set(component_predictions)
    assert sum(stacker.weights.values()) == pytest.approx(1.0)
    assert all(weight >= 0.0 for weight in stacker.weights.values())
    assert stacker.weights["kalshi"] >= stacker.weights["structural"]
    assert all(0.0 <= value <= 1.0 for value in predictions)
