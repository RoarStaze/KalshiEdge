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

    progress = []
    first = models.train_candidate_models(
        rows, folds, seed=73115,
        progress=lambda completed, total, action:
            progress.append((completed, total, action)),
    )
    second = models.train_candidate_models(rows, folds, seed=73115)
    expected = len(models.CANDIDATE_NAMES) * (len(folds) + 1)
    observed = [done for done, _, _ in progress]
    assert observed == sorted(observed)
    assert sorted(set(observed)) == list(range(expected + 1))
    assert observed[0] == 0 and observed[-1] == expected
    assert any("Optimizing" in action for _, _, action in progress)
    assert all(total == expected and action for _, total, action in progress)

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


def test_lockbox_only_feature_cannot_enter_development_feature_schema() -> None:
    rows = list(_rows())
    folds = _folds(tuple(rows))
    lockbox_group_indices = set(folds[0].lockbox_indices)
    for group_index in lockbox_group_indices:
        rows[group_index] = rows[group_index].model_copy(
            update={"features": {**rows[group_index].features, "future_lockbox_only_signal": 999.0}}
        )

    result = models.train_candidate_models(tuple(rows), folds, seed=73115)

    assert "future_lockbox_only_signal" not in result.feature_names


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
        "residual_corrected": [0.2, 0.8, 0.25, 0.75, 0.2, 0.8, 0.25, 0.75],
    }

    stacker = models.fit_stacker(component_predictions, labels)
    predictions = stacker.predict(component_predictions)

    assert set(stacker.weights) == set(component_predictions)
    assert sum(stacker.weights.values()) == pytest.approx(1.0)
    assert all(weight >= 0.0 for weight in stacker.weights.values())
    assert stacker.weights["kalshi"] >= stacker.weights["structural"]
    assert all(0.0 <= value <= 1.0 for value in predictions)


def test_zero_weight_stacker_component_can_be_omitted_at_inference() -> None:
    stacker = models.Stacker({"kalshi": 1.0, "residual_corrected": 0.0})

    assert stacker.predict({"kalshi": [0.2, 0.8]}) == pytest.approx([0.2, 0.8])

    with pytest.raises(models.ModelError, match="active component"):
        stacker.predict({"residual_corrected": [0.2, 0.8]})


def test_inner_tuning_partition_uses_substantial_bounded_chronological_window():
    """No more hyperparameter decisions based on just two markets out of 30k."""
    inner_train, inner_validation = models._inner_tuning_group_indices(tuple(range(18000)))
    assert len(inner_train) >= 17000
    assert len(inner_validation) == 512
    assert inner_train[-1] + 1 == inner_validation[0]
    assert inner_validation[-1] == 17999
    assert not set(inner_train).intersection(inner_validation)
    assert models._inner_tuning_group_indices(tuple(range(200)))[1] == tuple(range(160, 200))
    assert models._inner_tuning_group_indices(tuple(range(4))) == ((0, 1, 2), (3,))


def test_inner_checkpoint_tuning_scores_only_the_exact_480s_market_rows(monkeypatch):
    """Mixed-horizon rows must not tune 480s classification using later labels."""
    from types import SimpleNamespace
    seen_labels = []
    rows = []
    for i in range(30):
        for seconds in (480.0, 240.0):
            rows.append(FeatureRow(
                market_ticker=f"KXBTC15M-{i:03d}",
                split_group_id=f"KXBTC15M-{i:03d}",
                checkpoint_ts_ns=1_800_000_000_000_000_000 + i * 900 * NS + (900-int(seconds))*NS,
                label_yes=i % 2,
                features={"signal": float(i), "seconds_remaining": seconds},
                source_max_ts_ns={"binance": 1_800_000_000_000_000_000 + i * 900 * NS},
            ))
    all_rows = tuple(rows)
    groups = tuple(f"KXBTC15M-{i:03d}" for i in range(30))
    matrix = [[r.features["signal"], r.features["seconds_remaining"]] for r in all_rows]
    labels = [r.label_yes for r in all_rows]
    original_metrics = models.probability_metrics

    def spy_metrics(y, p):
        seen_labels.append((list(y), len(p)))
        return original_metrics(y, p)

    class FakeModel:
        def predict_proba(self, x):
            import numpy as np
            return np.array([[.4, .6] for _ in x])

    monkeypatch.setattr(models, "_fit_candidate", lambda *args, **kwargs: FakeModel())
    monkeypatch.setattr(models, "probability_metrics", spy_metrics)
    models._select_params(
        "logistic", all_rows, groups, tuple(range(30)),
        ("signal", "seconds_remaining"), seed=1,
        matrix=matrix, labels=labels,
    )
    assert len(seen_labels) == len(models._candidate_grid("logistic"))
    assert all(size == len(y) == 6 for y, size in seen_labels)


def test_training_candidate_matrix_is_constructed_once_for_all_inner_folds(monkeypatch):
    """Huge historical datasets must not rematerialize half a million rows for every grid trial."""
    rows = _rows()
    folds = _folds(rows)
    original = models._row_matrix
    calls = []

    def track(dataset, names):
        calls.append(len(dataset))
        return original(dataset, names)

    monkeypatch.setattr(models, "_row_matrix", track)
    result = models.train_candidate_models(rows, folds, seed=73115)
    assert result.best_candidate in models.CANDIDATE_NAMES
    assert calls == [len(rows)]
