from __future__ import annotations

import base64
import hashlib
import json
import pickle
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import artifact
from kalshi_edge.bootstrap.binance_history import BinanceBar
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.live_kalshi import BRTIObservation, LiveMarket, LiveQuote, LiveTrade
from kalshi_edge.bootstrap.models import ResidualModel, Stacker
from kalshi_edge.bootstrap.types import FeedQuality


NS = 1_000_000_000
BASE = 1_800_000_000_000_000_000
OPEN = BASE
CLOSE = OPEN + 900 * NS


class DummyClassifier:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, matrix):
        import numpy as np

        return np.asarray([[1.0 - self.probability, self.probability] for _ in matrix], dtype=float)


class DummyCalibrator:
    method = "identity"

    def predict(self, values):
        return list(values)


class DummyStructural:
    def __init__(self, normal: float = 0.64, final: float = 0.81) -> None:
        self.normal = normal
        self.final = final
        self.last_final_state = None

    def predict_proba(self, _state):
        return self.normal

    def predict_final_minute(self, state):
        self.last_final_state = state
        return self.final


class DummyPipeline:
    def __init__(self, required=("seconds_remaining", "strike", "btc_close", "btc_realized_vol_60s", "btc_return_5s", "btc_return_5s_available", "kalshi_mid", "kalshi_quote_available")) -> None:
        self.outcome_feature_names = ("btc_close",)
        self.required_feature_names = tuple(required)
        self.outcome_model_name = "dummy"
        self.outcome_model = DummyClassifier(0.67)
        self.logistic_model = DummyClassifier(0.61)
        self.structural_model = DummyStructural()
        self.residual_model = ResidualModel(
            intercept=0.0,
            coefficients=(0.0,),
            feature_means=(0.0,),
            feature_scales=(1.0,),
            component_weight=0.0,
        )
        self.stacker = Stacker({"kalshi_prior": 0.25, "historical_ml": 0.5, "structural": 0.25, "residual_corrected": 0.0})
        self.calibrator = DummyCalibrator()

    def predict(self, rows):
        # The direct pre-final-minute path is intentionally deterministic for tests.
        return {
            "candidate": [0.66 for _ in rows],
            "kalshi_prior": [0.60 for _ in rows],
            "structural": [0.64 for _ in rows],
            "logistic": [0.61 for _ in rows],
            "naive_50": [0.5 for _ in rows],
            "historical_ml": [0.67 for _ in rows],
            "residual_corrected": [0.60 for _ in rows],
        }


def _bundle(feature_names, *, stage="promoted", payload=b"pipeline") -> artifact.ModelBundle:
    digest = hashlib.sha256(payload).hexdigest()
    return artifact.ModelBundle(
        stage=stage,
        model_version="bootstrap-hybrid-v1",
        git_sha="a" * 40,
        random_seed=73115,
        feature_schema_version=1,
        feature_names=tuple(feature_names),
        boundaries=artifact.SplitBoundaries(
            train_start_ts_ns=1,
            train_end_ts_ns=2,
            calibration_start_ts_ns=3,
            calibration_end_ts_ns=4,
            lockbox_start_ts_ns=5,
            lockbox_end_ts_ns=6,
        ),
        input_hashes={"derived/features.parquet": "b" * 64},
        training_config_hash="c" * 64,
        library_versions={"python": "3.13"},
        components=(),
        calibration_method="identity",
        leakage_audit=artifact.LeakageEvidence(passed=True, finding_count=0),
        metrics={},
        serialized_pipeline_b64=base64.b64encode(payload).decode("ascii"),
        serialized_pipeline_sha256=digest,
        source_experiment_sha256="d" * 64 if stage == "promoted" else None,
        promotion_rule="test-rule" if stage == "promoted" else None,
    )


def _healthy_state(now_ns: int, *, final_minute: bool = False):
    from kalshi_edge.bootstrap.live import LiveFeatureState

    state = LiveFeatureState()
    state.update_market(LiveMarket(ticker="KXBTC15M-TEST", strike=60_000.0, open_ts_ns=OPEN, close_ts_ns=CLOSE, open_interest=17.0))

    # One completed historical minute supplies candle-compatible quote features.
    state.update_kalshi(LiveQuote(market_ticker="KXBTC15M-TEST", source_ts_ns=now_ns - 61 * NS, yes_bid=0.58, yes_ask=0.62))
    state.update_kalshi(LiveTrade(market_ticker="KXBTC15M-TEST", source_ts_ns=now_ns - 61 * NS, yes_price=0.60, count=2.0, taker_side="yes"))
    # Current diagnostics/staleness are independent of the completed candle contract.
    state.update_kalshi(LiveQuote(market_ticker="KXBTC15M-TEST", source_ts_ns=now_ns - NS, yes_bid=0.59, yes_ask=0.61))
    state.update_kalshi(LiveTrade(market_ticker="KXBTC15M-TEST", source_ts_ns=now_ns - NS, yes_price=0.60, count=1.0, taker_side="no"))

    first_bar = max(OPEN, now_ns - 700 * NS)
    for ts_ns in range(first_bar, now_ns, NS):
        seconds = (ts_ns - OPEN) // NS
        price = 59_900.0 + 0.20 * seconds
        state.update_binance(
            BinanceBar(
                ts_ns=ts_ns,
                open=price - 0.05,
                high=price + 0.10,
                low=price - 0.10,
                close=price,
                base_volume=1.0,
                quote_volume=price,
                trade_count=2,
                taker_buy_base=0.55,
                taker_buy_quote=price * 0.55,
            )
        )

    if not final_minute:
        state.update_brti(BRTIObservation(source_ts_ns=now_ns - NS, value=60_001.0))
    return state


def _predictor(state, *, features=None, required=None, kalshi_stale=5.0, binance_stale=5.0):
    from kalshi_edge.bootstrap.live import LivePredictor

    pipeline = DummyPipeline(required=required or tuple(features or DummyPipeline().required_feature_names))
    bundle = _bundle(features or pipeline.required_feature_names)
    return LivePredictor(
        state=state,
        bundle=bundle,
        pipeline=pipeline,
        settings=BootstrapSettings(kalshi_stale_seconds=kalshi_stale, binance_stale_seconds=binance_stale),
    )


def test_live_feature_state_reuses_causal_historical_feature_semantics() -> None:
    now = CLOSE - 120 * NS
    state = _healthy_state(now)
    row = state.feature_row(now)

    assert row.checkpoint_ts_ns == now
    assert row.features["seconds_remaining"] == 120.0
    assert row.features["strike"] == 60_000.0
    assert row.features["kalshi_quote_available"] == 1.0
    assert row.features["kalshi_mid"] == 0.60
    assert row.features["btc_close"] > 0.0
    assert max(row.source_max_ts_ns.values()) <= now


def test_predictor_fails_closed_for_required_live_health_and_schema_errors() -> None:
    from kalshi_edge.bootstrap.live import LiveFeatureState

    now = CLOSE - 120 * NS

    missing_market = LiveFeatureState()
    result = _predictor(missing_market).predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "MISSING_ACTIVE_MARKET"

    missing_brti = _healthy_state(now)
    missing_brti.brti_observations.clear()
    result = _predictor(missing_brti).predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "MISSING_BRTI"

    stale_quote = _healthy_state(now)
    stale_quote.latest_quote_ts_ns = now - 10 * NS
    result = _predictor(stale_quote).predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "STALE_KALSHI_BOOK"

    stale_binance = _healthy_state(now)
    stale_binance.latest_binance_ts_ns = now - 10 * NS
    result = _predictor(stale_binance).predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "STALE_BINANCE"

    schema = _healthy_state(now)
    result = _predictor(schema, features=(*DummyPipeline().required_feature_names, "trained_only_missing")).predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "FEATURE_SCHEMA_MISMATCH"

    model_mismatch = _healthy_state(now)
    predictor = _predictor(model_mismatch, features=DummyPipeline().required_feature_names, required=("btc_close",))
    result = predictor.predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "PROMOTED_MODEL_FEATURE_MISMATCH"


def test_predictor_rejects_inconsistent_market_quote_and_outside_market_window() -> None:
    now = CLOSE - 120 * NS
    state = _healthy_state(now)
    state.update_kalshi(LiveQuote(market_ticker="KXBTC15M-TEST", source_ts_ns=now, yes_bid=0.70, yes_ask=0.60))
    result = _predictor(state).predict(now)
    assert result.status == "NO_PREDICTION" and result.reason == "INCONSISTENT_KALSHI_QUOTE"

    state = _healthy_state(now)
    result = _predictor(state).predict(CLOSE + NS)
    assert result.status == "NO_PREDICTION" and result.reason == "NO_ACTIVE_MARKET_AT_TIMESTAMP"


def test_pre_final_minute_prediction_contains_full_diagnostics() -> None:
    now = CLOSE - 120 * NS
    state = _healthy_state(now)
    result = _predictor(state).predict(now)

    assert result.status == "OK"
    assert result.p_yes == pytest.approx(0.66)
    assert result.p_no == pytest.approx(0.34)
    assert result.predicted_side == "ABOVE"
    assert result.strike == 60_000.0
    assert result.seconds_remaining == 120.0
    assert result.kalshi_yes_bid == 0.59
    assert result.kalshi_yes_ask == 0.61
    assert result.kalshi_prior is not None
    assert result.brti_value == 60_001.0
    assert result.btc_reference is not None
    assert result.structural_probability == 0.64
    assert result.ml_probability == 0.67
    assert result.residual_probability == 0.60
    assert result.final_probability == pytest.approx(0.66)
    assert 0.0 <= result.confidence <= 1.0
    assert result.component_disagreement is not None
    assert result.model_hash is not None
    assert result.model_version == "bootstrap-hybrid-v1"
    assert result.feed_quality.healthy is True


def test_final_minute_uses_only_unique_contiguous_official_brti_observations() -> None:
    now = CLOSE - 30 * NS
    final_start = CLOSE - 60 * NS
    state = _healthy_state(now, final_minute=True)
    for second in range(30):
        state.update_brti(
            BRTIObservation(
                source_ts_ns=final_start + second * NS + 100_000_000,
                value=60_000.0 + second,
            )
        )

    predictor = _predictor(state)
    result = predictor.predict(now)
    assert result.status == "OK"
    assert result.structural_probability == pytest.approx(0.81)
    final_state = predictor.pipeline.structural_model.last_final_state
    assert final_state is not None
    assert final_state.elapsed_observations == 30
    assert [item.second_index for item in final_state.observations] == list(range(30))
    assert final_state.observations[-1].value == 60_029.0

    missing = _healthy_state(now, final_minute=True)
    for second in range(30):
        if second == 5:
            continue
        missing.update_brti(BRTIObservation(source_ts_ns=final_start + second * NS + 100_000_000, value=60_000.0))
    failed = _predictor(missing).predict(now)
    assert failed.status == "NO_PREDICTION"
    assert failed.reason == "INCOMPLETE_FINAL_MINUTE_BRTI"

    duplicate = _healthy_state(now, final_minute=True)
    for second in range(30):
        duplicate.update_brti(BRTIObservation(source_ts_ns=final_start + second * NS + 100_000_000, value=60_000.0))
    duplicate.update_brti(BRTIObservation(source_ts_ns=final_start + 29 * NS + 200_000_000, value=60_001.0))
    failed = _predictor(duplicate).predict(now)
    assert failed.status == "NO_PREDICTION"
    assert failed.reason == "AMBIGUOUS_FINAL_MINUTE_BRTI"


def test_default_loader_accepts_only_hash_verified_promoted_bundle(tmp_path: Path) -> None:
    from kalshi_edge.bootstrap.live import LiveModelError, load_default_bundle

    root = tmp_path / "bootstrap"
    promoted = _bundle(("btc_close",), stage="promoted")
    promoted_path = artifact.save_model_bundle(promoted, root / "models" / "promoted")
    sealed = artifact.load_model_bundle(promoted_path)
    default = root / "models" / "default.json"
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text(
        json.dumps({"bundle_sha256": sealed.bundle_sha256, "path": promoted_path.relative_to(root).as_posix()}),
        encoding="utf-8",
    )
    loaded = load_default_bundle(root)
    assert loaded.bundle_sha256 == sealed.bundle_sha256
    assert loaded.stage == "promoted"

    experiment = _bundle(("btc_close",), stage="experiment")
    experiment_path = artifact.save_model_bundle(experiment, root / "models" / "experiments")
    default.write_text(
        json.dumps({"bundle_sha256": experiment_path.stem, "path": experiment_path.relative_to(root).as_posix()}),
        encoding="utf-8",
    )
    with pytest.raises(LiveModelError):
        load_default_bundle(root)

    default.write_text(json.dumps({"bundle_sha256": "0" * 64, "path": "../escape.json"}), encoding="utf-8")
    with pytest.raises(LiveModelError):
        load_default_bundle(root)


def test_prediction_jsonl_writer_is_bootstrap_only_and_canonical(tmp_path: Path) -> None:
    from kalshi_edge.bootstrap.live import PredictionJsonlWriter

    now = CLOSE - 120 * NS
    record = _predictor(_healthy_state(now)).predict(now)
    writer = PredictionJsonlWriter(tmp_path / "bootstrap")
    path = writer.append(record)

    assert "predictions" in path.parts
    assert path.name == "predictions.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["status"] == "OK"
    assert payload["model_version"] == "bootstrap-hybrid-v1"
    assert payload["final_probability"] == pytest.approx(0.66)

    with pytest.raises(ValueError):
        PredictionJsonlWriter(tmp_path / "data" / "raw")


def test_predict_live_cli_dispatches_only_to_task9_entrypoint(monkeypatch) -> None:
    from kalshi_edge.cli import main

    called = []

    def fake_run_live():
        called.append(True)
        return 0

    # This import is intentionally expected to fail in the RED cycle until live.py exists.
    import kalshi_edge.bootstrap.live as live

    monkeypatch.setattr(live, "run_live", fake_run_live)
    assert main(["predict-live"]) == 0
    assert called == [True]
