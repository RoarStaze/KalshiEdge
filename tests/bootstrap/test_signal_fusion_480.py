"""Test-first contracts for a fitted, single-path 480s signal-fusion overlay.

Synthetic samples here are unit fixtures, never evidence of Kalshi profitability.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.signal_fusion_480 import (
    FEATURE_NAMES,
    FusionError,
    FusionModel,
    build_signal_vector,
    load_active_fusion,
    persist_fusion,
    summarize_decisions,
)


def _features() -> dict[str, float]:
    return {
        "brti_distance_to_strike_bps": 5.0,
        "brti_binance_gap_bps": 1.3,
        "btc_return_15s": 0.0005,
        "btc_return_15s_available": 1.0,
        "btc_return_60s": 0.001,
        "btc_return_60s_available": 1.0,
        "btc_taker_buy_imbalance_60s": 0.40,
        "btc_base_volume_60s": 70.0,
        "kalshi_yes_taker_imbalance_60s": 0.12,
    }


def _artifact() -> FusionModel:
    return FusionModel.build(
        model_hash="a" * 64,
        means=(0.0,) * len(FEATURE_NAMES),
        scales=(1.0,) * len(FEATURE_NAMES),
        coefficients=(0.3,) * len(FEATURE_NAMES),
        intercept=0.01,
        fit_markets=240,
        calibration_markets=80,
        evaluation_markets=100,
        evaluation={"brier_improvement": 0.001, "logloss_improvement": 0.001},
        training_through_ts_ns=100,
    )


def test_signal_vector_contains_base_flip_reversal_candles_and_signed_flow():
    features = _features()
    result = build_signal_vector(0.7565, features, risk_z=0.1)
    assert result is not None
    assert len(result) == len(FEATURE_NAMES)
    x = dict(zip(FEATURE_NAMES, result))
    assert x["base_logit"] == pytest.approx(math.log(0.7565 / 0.2435))
    assert x["brti_signed_risk_z"] == pytest.approx(0.1)
    assert x["near_strike_reversal_bps"] < 0
    assert x["signed_aggressor_volume_btc"] == pytest.approx(28)
    assert x["btc_return_15s_bps"] == pytest.approx(5)
    assert x["btc_return_60s_bps"] == pytest.approx(10)
    features["btc_taker_buy_imbalance_60s"] = -0.40
    negative_flow = build_signal_vector(0.7565, features, risk_z=0.1)
    assert negative_flow is not None
    assert negative_flow[FEATURE_NAMES.index("signed_aggressor_volume_btc")] == pytest.approx(-28)
    assert negative_flow[FEATURE_NAMES.index("base_logit")] == result[0]


@pytest.mark.parametrize("change", [
    {"brti_distance_to_strike_bps": float("nan")},
    {"brti_distance_to_strike_bps": None},
    {"btc_return_15s_available": 0.0},
    {"btc_taker_buy_imbalance_60s": 1.1},
    {"btc_base_volume_60s": -10.0},
    {"brti_binance_gap_bps": float("inf")},
])
def test_invalid_or_missing_signal_fails_to_original_model(change):
    features = _features()
    features.update(change)
    assert build_signal_vector(0.75, features, risk_z=0.2) is None


def test_missing_flip_risk_is_not_fabricated():
    assert build_signal_vector(0.75, _features(), risk_z=None) is None
    assert build_signal_vector(0.75, _features(), risk_z=float("nan")) is None


def test_fitted_probability_is_bounded_and_changes_with_multiple_signals():
    model = _artifact()
    x = build_signal_vector(0.7565, _features(), risk_z=0.1)
    assert x is not None
    p = model.predict(x)
    assert 0 < p < 1
    flipped = _features()
    flipped["btc_taker_buy_imbalance_60s"] = -0.9
    other = build_signal_vector(0.7565, flipped, risk_z=2.5)
    assert other is not None
    assert model.predict(other) != pytest.approx(p)


def test_artifact_is_bound_to_model_hash_and_detects_tampering(tmp_path: Path):
    model = _artifact()
    persist_fusion(tmp_path, model)
    loaded = load_active_fusion(tmp_path, "a" * 64)
    assert loaded == model
    assert load_active_fusion(tmp_path, "b" * 64) is None
    path = tmp_path / "models" / "fusion" / ("a" * 64 + ".json")
    path.write_text(path.read_text().replace('"intercept":0.01', '"intercept":0.91'))
    with pytest.raises(FusionError):
        load_active_fusion(tmp_path, "a" * 64)


def test_noncausal_snapshot_input_cannot_be_inferred_from_future():
    features = _features()
    del features["brti_binance_gap_bps"]
    assert build_signal_vector(.75, features, risk_z=1) is None


def test_holdout_decisions_use_same_flip_veto_and_63_strength_gate():
    base = [0.9, 0.9, 0.8, 0.1]
    fused = [0.95, 0.88, 0.9, 0.1]
    actual = [1, 1, 1, 0]
    risk = [1.0, 0.1, 1.0, 1.0]
    base_counts = summarize_decisions(base, actual, risk)
    fused_counts = summarize_decisions(fused, actual, risk)
    assert base_counts["qualified"] == 2
    assert fused_counts["qualified"] == 3
    assert fused_counts["correct"] == 3
