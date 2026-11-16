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


def test_holdout_decisions_use_same_flip_veto_and_live_69_directional_gate():
    base = [0.9, 0.9, 0.8, 0.1]
    fused = [0.95, 0.88, 0.9, 0.1]
    actual = [1, 1, 1, 0]
    risk = [1.0, 0.1, 1.0, 1.0]
    base_counts = summarize_decisions(base, actual, risk)
    fused_counts = summarize_decisions(fused, actual, risk)
    assert base_counts["qualified"] == 3
    assert fused_counts["qualified"] == 3
    assert base_counts["correct"] == 3
    assert fused_counts["correct"] == 3


def test_fusion_holdout_uses_live_directional_probability_gate_not_strength_gate():
    """This must disagree with the obsolete strength-based acceptance metric."""
    probabilities = [0.690001, 0.309999, 0.69, 0.31, 0.90, 0.75]
    actual = [1, 0, 1, 0, 1, 1]
    risk = [1.0, 1.0, 1.0, 1.0, 0.20, 1.0]
    summary = summarize_decisions(probabilities, actual, risk)

    # 0.690001 and 0.309999 qualify; equality at 0.69 and its NO
    # complement (0.31) do not; high coin-flip risk vetoes 0.90.
    # The final 0.75 is valid under the real >0.69 directional gate.
    assert summary["qualified"] == 3
    assert summary["correct"] == 3
    assert summary["coverage"] == pytest.approx(0.5)


def test_fusion_new_artifacts_mark_the_current_policy_version():
    from kalshi_edge.bootstrap.signal_fusion_480 import POLICY

    assert POLICY == "live-480-fitted-multisignal-v3"
    assert _artifact().policy == POLICY


def test_legacy_v1_fusion_artifact_is_rejected_after_policy_gate_changes(tmp_path: Path):
    """A correctly signed old artifact still used an invalid live acceptance gate."""
    import hashlib
    import json

    current = _artifact().model_dump(mode="json")
    current["policy"] = "live-480-fitted-multisignal-v1"
    content_to_sign = {key: value for key, value in current.items() if key != "sha256"}
    encoded = json.dumps(content_to_sign, sort_keys=True, separators=(",", ":"), allow_nan=False)
    current["sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    path = tmp_path / "models" / "fusion" / ("a"*64 + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(current), encoding="utf-8")
    with pytest.raises(FusionError, match="legacy|obsolete|v1"):
        load_active_fusion(tmp_path, "a"*64)


@pytest.mark.parametrize("risk", [100.00001, 500.0, 1e9])
def test_valid_large_finite_brti_distance_is_retained_and_bounded(risk):
    """Strike distance may exceed 100 during very low realized volatility."""
    vector = build_signal_vector(.75, _features(), risk_z=risk)
    assert vector is not None
    assert all(math.isfinite(x) for x in vector)
    assert vector[FEATURE_NAMES.index("brti_signed_risk_z")] == 10.
    results = summarize_decisions([.75], [1], [risk])
    assert results["qualified"] == 1 and results["correct"] == 1


@pytest.mark.parametrize("risk", [-1., float("inf"), -float("inf"), True])
def test_bad_or_nonfinite_brti_risk_remains_invalid(risk):
    assert build_signal_vector(.75, _features(), risk_z=risk) is None
    with pytest.raises(FusionError, match="risk"):
        summarize_decisions([.75], [1], [risk])


def test_valid_signed_fusion_v2_artifact_is_not_silently_reused(tmp_path: Path):
    """Expanded risk-domain scoring needs fresh v3 holdout validation."""
    import hashlib
    import json

    payload = _artifact().model_dump(mode="json")
    payload["policy"] = "live-480-fitted-multisignal-v2"
    unsigned = {k: v for k, v in payload.items() if k != "sha256"}
    raw = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    path = tmp_path / "models" / "fusion" / ("a" * 64 + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FusionError, match="v2|legacy|refit|re-fit"):
        load_active_fusion(tmp_path, "a" * 64)


def _fusion_case(
    root: Path, index: int, *, risk: float | None = 1.0,
    include_snapshot: bool = True, include_settlement: bool = True,
    missing_signals: bool = False,
) -> None:
    from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
    from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter, build_forward_snapshot,
    )
    from kalshi_edge.bootstrap.types import FeatureRow

    ns = 1_000_000_000
    ts = (1_900_000_000 + index * 900) * ns
    ticker = f"KXBTC15M-FUSION-{index:04d}"
    close = ts + 480 * ns
    forecast = FinalForecastRecord(
        market_ticker=ticker, strike=100000., scheduled_ts_ns=ts,
        prediction_ts_ns=ts, checkpoint_seconds=480, seconds_remaining=480.,
        forecast="ABOVE", p_yes=.85, p_no=.15,
        directional_probability=.85, confidence=.70,
        coin_flip_risk_z=risk,
        coin_flip_assessment="LOW" if risk is not None else "UNAVAILABLE",
        model_hash="a" * 64,
    )
    path = FinalForecastWriter(root).save(forecast, market_close_ts_ns=close)
    assert path is not None
    if include_snapshot:
        feature_values = _features()
        if missing_signals:
            feature_values.pop("btc_return_60s_available")
        row = FeatureRow(
            market_ticker=ticker, checkpoint_ts_ns=ts, label_yes=0,
            features=feature_values,
            source_max_ts_ns={"binance": ts - ns, "brti": ts - ns},
        )
        Forward480FeatureWriter(root).save(
            build_forward_snapshot(forecast, path, row)
        )
    if include_settlement:
        _save_settlement(root, forecast, OfficialSettlement(
            market_ticker=ticker, market_close_ts_ns=close,
            result="yes", actual_side="ABOVE",
            source="kalshi-public-market-api",
            fetched_ts_ns=close + 60 * ns, market_status="settled",
        ))


def test_fusion_readiness_explains_missing_cases_without_fake_data(tmp_path: Path):
    from kalshi_edge.bootstrap.signal_fusion_480 import _verified_examples

    _fusion_case(tmp_path, 1, risk=500.)
    _fusion_case(tmp_path, 2, include_snapshot=False)
    _fusion_case(tmp_path, 3, include_settlement=False)
    _fusion_case(tmp_path, 4, risk=None)
    _fusion_case(tmp_path, 5, missing_signals=True)

    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    rows, counts = _verified_examples(tmp_path, "a" * 64)
    assert len(rows) == 1 and rows[0].risk_z == 500.
    assert counts["same_model_finals"] == 5
    assert counts["eligible"] == 1
    assert counts["missing_or_invalid"] == 4
    assert counts["exclusion_reasons"] == {
        "snapshot_missing_or_invalid": 1,
        "snapshot_unavailable": 0,
        "settlement_missing": 1,
        "settlement_invalid": 0,
        "probability_missing_or_invalid": 0,
        "risk_missing_or_invalid": 1,
        "signal_inputs_missing_or_invalid": 1,
        "duplicate_ticker": 0,
    }
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")} == before
