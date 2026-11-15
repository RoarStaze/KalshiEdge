"""Holdout scorer must accelerate independent structural paths with exact parity."""
from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from kalshi_edge.bootstrap import evaluate, models, structural, training, training_cycle
from kalshi_edge.bootstrap.types import FeatureRow


class ConstantClassifier:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, matrix):
        return np.array([[1-self.probability, self.probability] for _ in matrix])


class IdentityCalibrator:
    def predict(self, probabilities):
        return list(probabilities)


class FailingStructural:
    def predict_proba(self, _row):
        raise AssertionError("zero-weight structural component should not be computed")


def _rows(n: int = 40):
    return tuple(FeatureRow(
        market_ticker=f"MARKET-{i:03d}",
        split_group_id=f"MARKET-{i:03d}",
        market_date="2026-11-14",
        checkpoint_ts_ns=1_800_000_000_000_000_000 + i * 900_000_000_000,
        label_yes=i%2,
        features={
            "btc_close": 65000.0+i*2,
            "strike": 65025.0,
            "seconds_remaining": float(65+i%10),
            "btc_realized_vol_60s": .0005,
            "btc_return_5s": .0002,
            "btc_return_5s_available": 1.0,
            "kalshi_mid": .57,
            "kalshi_quote_available": 1.0,
        },
        source_max_ts_ns={},
    ) for i in range(n))


def _pipe(*, structural_weight=.25, structural_model=None):
    return training.FittedBootstrapPipeline(
        outcome_feature_names=("btc_close",),
        required_feature_names=(
            "btc_close","strike","seconds_remaining","btc_realized_vol_60s",
            "btc_return_5s","btc_return_5s_available",
            "kalshi_mid","kalshi_quote_available",
        ),
        outcome_model_name="dummy",
        outcome_model=ConstantClassifier(.65),
        logistic_model=ConstantClassifier(.61),
        structural_model=structural_model or structural.StructuralModel(
            candidate="diffusion", seed=73115,
            simulations=16, drift_per_second=0.0,
            drift_cap_per_second=.01, residuals=(-1., 0., 1.),
        ),
        residual_model=models.ResidualModel(
            intercept=0., coefficients=(0.0,),
            feature_means=(0.0,), feature_scales=(1.0,), component_weight=0.,
        ),
        stacker=models.Stacker({
            "kalshi_prior": .25,
            "historical_ml": .75 - structural_weight,
            "structural": structural_weight,
            "residual_corrected": 0.,
        }),
        calibrator=IdentityCalibrator(),
    )


def test_full_holdout_and_candidate_only_parallel_equal_serial_exactly():
    pipe = _pipe()
    rows = _rows()
    serial = pipe.predict(rows)
    only = pipe.base_candidate(rows)
    assert serial["candidate"] == only
    updates = []
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        parallel = pipe.predict(
            rows, structural_executor=pool,
            structural_progress=lambda complete,total: updates.append((complete,total)),
        )
        parallel_only = pipe.base_candidate(rows, structural_executor=pool)
    assert parallel == serial
    assert parallel_only == only
    assert updates[-1] == (40,40)
    assert all(0<done<=total for done,total in updates)


def test_candidate_only_skips_unused_monte_carlo_and_matches_full_candidate():
    rows = _rows()
    pipe = _pipe(structural_weight=0., structural_model=FailingStructural())
    # Full diagnostic output still computes structural and must not be changed.
    with pytest.raises(AssertionError,match="zero-weight"):
        pipe.predict(rows)
    scores=pipe.base_candidate(rows)
    assert len(scores) == len(rows)
    assert all(0 <= p <= 1 for p in scores)


def test_evaluation_worker_count_respects_existing_windows_headroom(monkeypatch):
    monkeypatch.delenv("KALSHI_TRAIN_STRUCTURAL_WORKERS", raising=False)
    assert evaluate._holdout_worker_count(platform="win32",cpu_count=12) == 3
    assert evaluate._holdout_worker_count(platform="linux",cpu_count=12) == 1
    monkeypatch.setenv("KALSHI_TRAIN_STRUCTURAL_WORKERS","4")
    assert evaluate._holdout_worker_count(platform="win32",cpu_count=12) == 4


def test_scoring_progress_records_real_rows_without_fake_percentage(tmp_path):
    seen = []
    reporter = training_cycle._holdout_scoring_progress_reporter(
        tmp_path, mode="baseline", round_number=0,
    )
    reporter("SCORING", 32, 100, "Candidate: 32 structural rows")
    state = training_cycle._run_state(tmp_path)
    assert state["stage"] == "SCORING"
    assert state["progress_percent"] == 67
    assert state["score_rows_completed"] == 32
    assert state["score_rows_total"] == 100
    assert state["score_detail"] == "Candidate: 32 structural rows"
    reporter("COMPARING", 40, 40, "Incumbent 480s")
    second = training_cycle._run_state(tmp_path)
    assert second["stage"] == "COMPARING"
    assert second["progress_percent"] == 83
    assert second["score_rows_completed"] == 40
    assert second["score_rows_total"] == 40
    assert seen == []


def test_scoring_progress_rejects_fake_or_out_of_bounds_counts(tmp_path):
    reporter=training_cycle._holdout_scoring_progress_reporter(tmp_path,mode="baseline",round_number=0)
    for done,total in [(10,9),(-1,9),(0,0),(True,3)]:
        with pytest.raises(ValueError):
            reporter("SCORING",done,total,"bad progress")
    assert training_cycle._run_state(tmp_path) is None
