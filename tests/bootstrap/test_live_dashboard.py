from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.live_dashboard import read_live_dashboard
from kalshi_edge.bootstrap.types import FeedQuality

NS = 1_000_000_000
CLOSE = 1_800_000_900_000_000_000
DUE = CLOSE - 240 * NS
TICKER = "KXBTC15M-26OCT280430-30"


def _write_prediction(root: Path, at: int, *, status: str = "OK", seconds: float | None = None):
    remaining = seconds if seconds is not None else (CLOSE - at) / NS
    record = LivePredictionRecord(
        prediction_ts_ns=at,
        market_ticker=TICKER,
        status=status,
        feed_quality=FeedQuality(healthy=status == "OK"),
        model_hash="f" * 64,
        model_version="bootstrap-hybrid-v5",
        strike=81043.29,
        seconds_remaining=remaining,
        p_yes=0.89 if status == "OK" else None,
        p_no=0.11 if status == "OK" else None,
        predicted_side="ABOVE" if status == "OK" else None,
        confidence=0.78 if status == "OK" else None,
        kalshi_prior=0.80 if status == "OK" else None,
        btc_reference=81200.0,
    )
    day = datetime.fromtimestamp(at / NS, tz=timezone.utc).date().isoformat()
    path = root / "predictions" / day / "predictions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(record.model_dump_json() + "\n")


def test_unified_dashboard_waits_without_fresh_predictions(tmp_path: Path):
    assert read_live_dashboard(tmp_path, now_ns=DUE)["status"] == "WAITING_FOR_PREDICTOR"
    assert read_live_dashboard(tmp_path, now_ns=DUE)["latest_prediction"] is None


def test_unified_dashboard_reads_live_preview_without_fabricating_lock(tmp_path: Path):
    now = DUE - 20 * NS
    _write_prediction(tmp_path, now - NS)
    snapshot = read_live_dashboard(tmp_path, now_ns=now)
    assert snapshot["status"] == "LIVE"
    assert snapshot["market_ticker"] == TICKER
    assert snapshot["strike"] == 81043.29
    assert snapshot["latest_prediction"]["confidence"] == pytest.approx(0.78)
    assert snapshot["latest_prediction"]["predicted_side"] == "ABOVE"
    assert snapshot["final_forecast"] is None
    assert snapshot["checkpoint_ts_ns"] == DUE
    assert snapshot["minimum_confidence"] == 0.69


def test_locked_checkpoint_uses_immutable_final_record(tmp_path: Path):
    now = DUE + NS
    _write_prediction(tmp_path, now - NS // 10, seconds=(CLOSE - (now - NS // 10)) / NS)
    forecast = FinalForecastRecord(
        market_ticker=TICKER,
        strike=81043.29,
        scheduled_ts_ns=DUE,
        prediction_ts_ns=DUE + NS // 3,
        seconds_remaining=239.667,
        forecast="BELOW",
        confidence=0.96,
        directional_probability=0.98,
        p_yes=0.02,
        p_no=0.98,
        model_hash="f" * 64,
        model_version="bootstrap-hybrid-v5",
    )
    assert FinalForecastWriter(tmp_path).save(forecast, market_close_ts_ns=CLOSE) is not None
    snapshot = read_live_dashboard(tmp_path, now_ns=now)
    assert snapshot["status"] == "LIVE"
    assert snapshot["final_forecast"]["forecast"] == "BELOW"
    assert snapshot["final_forecast"]["confidence"] == pytest.approx(0.96)
    assert snapshot["market_close_ts_ns"] == CLOSE


def test_float_rounding_does_not_hide_matching_saved_forecast(tmp_path: Path):
    now = DUE + NS
    _write_prediction(tmp_path, now - NS // 2, seconds=(CLOSE - (now - NS // 2)) / NS + 0.0000004)
    forecast = FinalForecastRecord(
        market_ticker=TICKER, strike=81043.29,
        scheduled_ts_ns=DUE, prediction_ts_ns=DUE + NS // 3,
        seconds_remaining=239.667, forecast="SKIP",
        reason="CONFIDENCE_BELOW_69_PERCENT",
        model_hash="f" * 64,
    )
    FinalForecastWriter(tmp_path).save(forecast, market_close_ts_ns=CLOSE)
    snapshot = read_live_dashboard(tmp_path, now_ns=now)
    assert snapshot["final_forecast"]["forecast"] == "SKIP"
    assert snapshot["market_close_ts_ns"] == CLOSE


def test_stale_prediction_is_never_displayed_as_live(tmp_path: Path):
    _write_prediction(tmp_path, DUE - 50 * NS)
    result = read_live_dashboard(tmp_path, now_ns=DUE)
    assert result["status"] == "WAITING_FOR_PREDICTOR"
    assert result["market_ticker"] is None
    assert result["latest_prediction"] is None


def test_feed_degradation_is_not_reported_healthy(tmp_path: Path):
    _write_prediction(tmp_path, DUE - NS, status="NO_PREDICTION")
    result = read_live_dashboard(tmp_path, now_ns=DUE)
    assert result["status"] == "DEGRADED"
    assert result["final_forecast"] is None


def test_invalid_jsonl_tail_does_not_break_history_view(tmp_path: Path):
    _write_prediction(tmp_path, DUE - NS)
    day = datetime.fromtimestamp((DUE - NS) / NS, tz=timezone.utc).date().isoformat()
    with (tmp_path / "predictions" / day / "predictions.jsonl").open("a") as stream:
        stream.write('{"half-written":\n')
    assert read_live_dashboard(tmp_path, now_ns=DUE)["status"] == "LIVE"
