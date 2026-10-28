from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from kalshi_edge.bootstrap import models, training, volatility, volatility_upgrade
from kalshi_edge.bootstrap.types import FeatureRow


class ConstantClassifier:
    def predict_proba(self, matrix):
        return np.asarray([[0.4, 0.6] for _ in matrix])


class IdentityCalibrator:
    def predict(self, probabilities):
        return list(probabilities)


class ConstantStructural:
    def predict_proba(self, state):
        return 0.5


class ConstantResidual:
    def predict(self, matrix, prior):
        return [0.5] * len(matrix)


def _row():
    return FeatureRow(
        market_ticker="KXBTC15M-TEST",
        checkpoint_ts_ns=1_800_000_000_000_000_000,
        label_yes=1,
        features={
            "btc_close": 100.5, "strike": 100.0,
            "seconds_remaining": 240.0,
            "btc_realized_vol_60s": .0004,
            "btc_realized_vol_300s": .0004,
            "kalshi_mid": .5,
            "kalshi_quote_available": 1.0,
            "btc_return_5s": 0.0,
            "btc_return_5s_available": 1.0,
        },
        source_max_ts_ns={"binance": 1_800_000_000_000_000_000},
    )


def _pipeline():
    return training.FittedBootstrapPipeline(
        outcome_feature_names=("btc_close",),
        required_feature_names=(
            "btc_close", "strike", "seconds_remaining",
            "btc_realized_vol_60s", "btc_realized_vol_300s", "kalshi_mid",
        ),
        outcome_model_name="hist_gradient_boosting",
        outcome_model=ConstantClassifier(),
        logistic_model=ConstantClassifier(),
        structural_model=ConstantStructural(),
        residual_model=ConstantResidual(),
        stacker=models.Stacker({"historical_ml": 1.0}),
        calibrator=IdentityCalibrator(),
    )


def test_old_v5_without_overlay_produces_same_fast_and_full_candidate():
    row = _row()
    pipeline = _pipeline()
    assert pipeline.base_candidate([row]) == pytest.approx([.6])
    assert pipeline.predict([row])["candidate"] == pytest.approx([.6])


def test_promoted_volatility_overlay_changes_candidate_not_incumbent_components():
    row = _row()
    incumbent = _pipeline()
    enhanced = replace(incumbent, volatility_overlay=volatility.VolatilityOverlay(.20))
    calibrated = incumbent.predict([row])
    adjusted = enhanced.predict([row])
    assert adjusted["candidate"][0] > calibrated["candidate"][0]
    assert enhanced.base_candidate([row]) == pytest.approx(adjusted["candidate"])
    assert adjusted["historical_ml"] == calibrated["historical_ml"]
    assert adjusted["volatility_analytic"][0] > calibrated["candidate"][0]


def test_promoted_overlay_does_not_modify_last_minute_without_final_averaging_context():
    row = _row().model_copy(update={
        "features": {**_row().features, "seconds_remaining": 45.0}
    })
    enhanced = replace(_pipeline(), volatility_overlay=volatility.VolatilityOverlay(.20))
    # FittedBootstrapPipeline.predict itself requires >=60. The overlay is
    # deliberately identity for <60; LivePredictor uses separate final-minute
    # state, preserving existing causality rules.
    assert enhanced.volatility_overlay.predict(.6, row.features) == pytest.approx(.6)


def test_preflight_refuses_used_or_absent_new_lockbox(tmp_path):
    state = volatility_upgrade.preflight(tmp_path)
    assert state["ready"] is False
    assert "previously evaluated" in state["reason"]


def test_volatility_upgrade_path_is_an_explicit_separate_challenger_not_3_1_retraining():
    assert volatility_upgrade.UPGRADE_MARKER_DIR == "volatility_challengers"
    assert volatility_upgrade.MIN_FRESH_LOCKBOX_MARKETS >= 80
    assert 240.0 == volatility.FINAL_LOCK_SECONDS
    assert volatility_upgrade.UPGRADE_MARKER_DIR not in ("mistake_pool", "correct_pool")
