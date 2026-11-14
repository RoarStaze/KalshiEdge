"""Selection of historical models must match KalshiEdge's 480s final forecast."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from kalshi_edge.bootstrap import models, splits, training
from kalshi_edge.bootstrap.types import FeatureRow

NS = 1_000_000_000
BASE = 1_800_000_000_000_000_000


def _mixed_checkpoint_rows(markets: int = 100) -> tuple[FeatureRow, ...]:
    rows: list[FeatureRow] = []
    for market in range(markets):
        label = market % 2
        for seconds in (480.0, 420.0, 300.0, 240.0):
            rows.append(
                FeatureRow(
                    market_ticker=f"KXBTC15M-M{market:04d}",
                    split_group_id=f"KXBTC15M-M{market:04d}",
                    market_date="2026-11-14",
                    checkpoint_ts_ns=BASE + market * 900 * NS + (900 - int(seconds)) * NS,
                    label_yes=label,
                    features={
                        "seconds_remaining": seconds,
                        "signal": 1.0 if label else -1.0,
                    },
                    source_max_ts_ns={"binance": BASE + market * 900 * NS},
                )
            )
    return tuple(rows)


def _forecasts(name: str, rows: tuple[FeatureRow, ...]) -> tuple[float, ...]:
    probabilities = []
    for row in rows:
        label = row.label_yes
        at_final = row.features["seconds_remaining"] == 480.0
        if name == "hist_gradient_boosting":
            correct = at_final
        elif name == "logistic":
            correct = not at_final
        else:
            probabilities.append(0.5)
            continue
        probabilities.append((0.9 if label else 0.1) if correct else (0.1 if label else 0.9))
    return tuple(probabilities)


def test_candidate_champion_uses_actual_480s_oof_even_when_other_horizons_disagree(monkeypatch):
    rows = _mixed_checkpoint_rows()
    market_index = [
        splits.MarketIndex(
            market_ticker=f"KXBTC15M-M{i:04d}",
            split_group_id=f"KXBTC15M-M{i:04d}",
            first_checkpoint_ts_ns=BASE + i * 900 * NS + 420 * NS,
        )
        for i in range(100)
    ]
    folds = splits.make_walk_forward_splits(
        market_index, min_train_markets=10, validation_markets=20,
        embargo_markets=0,
    )

    class FakeEstimator:
        def __init__(self, name: str):
            self.name = name

        def predict_proba(self, matrix):
            # Feature order is deterministic: seconds_remaining then signal.
            probabilities = []
            for seconds, signal in matrix:
                label = signal > 0.0
                if self.name == "hist_gradient_boosting":
                    correct = seconds == 480.0
                elif self.name == "logistic":
                    correct = seconds != 480.0
                else:
                    probabilities.append(0.5)
                    continue
                probabilities.append((0.9 if label else 0.1) if correct else (0.1 if label else 0.9))
            p = np.asarray(probabilities)
            return np.column_stack((1.0 - p, p))

    monkeypatch.setattr(
        models, "_fit_candidate",
        lambda name, params, x, y, seed: FakeEstimator(name),
    )
    results = models.train_candidate_models(rows, folds, seed=73115)

    assert results.best_candidate == "hist_gradient_boosting"
    assert results.oof_metrics["hist_gradient_boosting"].brier < 0.02
    assert results.oof_metrics["logistic"].brier > 0.8


def test_later_feature_and_xgb_ablation_scores_use_only_480s_market_snapshots():
    rows = _mixed_checkpoint_rows()
    scores = {
        name: _forecasts(name, rows)
        for name in ("hist_gradient_boosting", "logistic")
    }
    result = SimpleNamespace(best_candidate="hist_gradient_boosting", oof_predictions=scores)

    chosen = training._later_metrics(result, rows, "hist_gradient_boosting")
    rejected = training._later_metrics(result, rows, "logistic")

    assert chosen.brier < 0.02
    assert rejected.brier > 0.8


def test_sparse_480s_oof_cohort_does_not_replace_stable_all_horizon_selection():
    rows = _mixed_checkpoint_rows(markets=15)
    predictions = _forecasts("hist_gradient_boosting", rows)
    positions = models._oof_scoring_positions(rows, predictions)

    assert positions == list(range(len(rows)))
