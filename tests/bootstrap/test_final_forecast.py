from __future__ import annotations

import json

import pytest

from kalshi_edge.bootstrap.final_forecast import (
    FINAL_CHECKPOINT_SECONDS,
    MINIMUM_FINAL_CONFIDENCE,
    FinalForecastGate,
    FinalForecastWriter,
)
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.live_kalshi import LiveMarket
from kalshi_edge.bootstrap.types import FeedQuality


NS = 1_000_000_000
OPEN = 1_800_000_000_000_000_000
CLOSE = OPEN + 900 * NS
DUE = CLOSE - 240 * NS
TICKER = "KXBTC15M-TEST"


def _market(ticker: str = TICKER) -> LiveMarket:
    return LiveMarket(ticker=ticker, strike=60000.0, open_ts_ns=OPEN, close_ts_ns=CLOSE)


def _prediction(
    now: int, *, probability: float = 0.90, confidence: float = 0.80,
    status: str = "OK", reason: str | None = None,
    ticker: str = TICKER, healthy: bool = True,
) -> LivePredictionRecord:
    if status != "OK":
        return LivePredictionRecord(
            prediction_ts_ns=now,
            market_ticker=ticker,
            status="NO_PREDICTION",
            reason=reason or "MISSING_KALSHI_BOOK",
            feed_quality=FeedQuality(healthy=False, reasons=(reason or "MISSING_KALSHI_BOOK",)),
            model_hash="sealed-sha",
            model_version="bootstrap-hybrid-v5",
        )
    return LivePredictionRecord(
        prediction_ts_ns=now,
        market_ticker=ticker,
        status="OK",
        p_yes=probability,
        p_no=1 - probability,
        predicted_side="ABOVE" if probability >= 0.5 else "BELOW",
        feed_quality=FeedQuality(healthy=healthy),
        model_hash="sealed-sha",
        model_version="bootstrap-hybrid-v5",
        confidence=confidence,
        final_probability=probability,
    )


def test_final_forecast_is_not_published_early_and_locks_once_at_240s() -> None:
    gate = FinalForecastGate()
    market = _market()
    assert FINAL_CHECKPOINT_SECONDS == 240
    assert gate.evaluate(market, now_ns=DUE - 1, prediction=_prediction(DUE - 1)) is None
    final = gate.evaluate(market, now_ns=DUE, prediction=_prediction(DUE))
    assert final is not None
    assert final.market_ticker == TICKER
    assert final.forecast == "ABOVE"
    assert final.reason is None
    assert final.scheduled_ts_ns == DUE
    assert final.seconds_remaining == pytest.approx(240.0)
    assert final.directional_probability == pytest.approx(0.9)
    # A same-ticker feed reconnect must not create a second final decision.
    assert gate.evaluate(_market(), now_ns=DUE + NS, prediction=_prediction(DUE + NS)) is None


def test_final_forecast_skip_rule_exact_threshold_and_below() -> None:
    assert MINIMUM_FINAL_CONFIDENCE == 0.69
    below = FinalForecastGate().evaluate(
        _market(), now_ns=DUE,
        prediction=_prediction(DUE, probability=0.8, confidence=0.689),
    )
    assert below.forecast == "SKIP"
    assert below.reason == "CONFIDENCE_BELOW_69_PERCENT"
    assert below.confidence == pytest.approx(0.689)
    assert below.directional_probability == pytest.approx(0.8)

    boundary = FinalForecastGate().evaluate(
        _market(), now_ns=DUE,
        prediction=_prediction(DUE, probability=0.845, confidence=0.69),
    )
    assert boundary.forecast == "ABOVE"
    assert boundary.reason is None

    below_side = FinalForecastGate().evaluate(
        _market(), now_ns=DUE,
        prediction=_prediction(DUE, probability=0.1, confidence=0.8),
    )
    assert below_side.forecast == "BELOW"
    assert below_side.directional_probability == pytest.approx(0.9)


def test_final_forecast_unhealthy_or_missing_prediction_fails_closed() -> None:
    market = _market()
    unhealthy = FinalForecastGate().evaluate(
        market, now_ns=DUE, prediction=_prediction(
            DUE, status="NO_PREDICTION", reason="STALE_BINANCE",
        ),
    )
    assert unhealthy.forecast == "SKIP"
    assert unhealthy.reason == "STALE_BINANCE"

    missing = FinalForecastGate().evaluate(market, now_ns=DUE, prediction=None)
    assert missing.forecast == "SKIP"
    assert missing.reason == "MISSING_CHECKPOINT_PREDICTION"

    mismatched = FinalForecastGate().evaluate(
        market, now_ns=DUE, prediction=_prediction(DUE, ticker="KXBTC15M-OTHER"),
    )
    assert mismatched.forecast == "SKIP"
    assert mismatched.reason == "PREDICTION_MARKET_MISMATCH"

    stale = FinalForecastGate().evaluate(
        market, now_ns=DUE, prediction=_prediction(DUE - NS),
    )
    assert stale.forecast == "SKIP"
    assert stale.reason == "MISSING_CHECKPOINT_PREDICTION"


def test_final_forecast_never_backdates_or_fabricates_late_decision() -> None:
    market = _market()
    permitted = FinalForecastGate().evaluate(
        market, now_ns=DUE + NS,
        prediction=_prediction(DUE + NS),
    )
    assert permitted.forecast == "ABOVE"
    assert permitted.prediction_ts_ns == DUE + NS
    assert permitted.seconds_remaining == pytest.approx(239)

    missed = FinalForecastGate().evaluate(
        market, now_ns=DUE + 3 * NS,
        prediction=_prediction(DUE + 3 * NS),
    )
    assert missed.forecast == "SKIP"
    assert missed.reason == "MISSED_240S_CHECKPOINT"
    assert missed.prediction_ts_ns == DUE + 3 * NS


def test_final_forecast_writer_is_immutable_across_restart(tmp_path) -> None:
    market = _market()
    record = FinalForecastGate().evaluate(
        market, now_ns=DUE, prediction=_prediction(DUE),
    )
    writer = FinalForecastWriter(tmp_path / "bootstrap")
    path = writer.save(record, market_close_ts_ns=CLOSE)
    assert path is not None and path.exists()
    assert path.parent.parent.name == "final_forecasts"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["forecast"] == "ABOVE"
    assert data["market_ticker"] == TICKER
    assert data["scheduled_ts_ns"] == DUE

    alternative = FinalForecastGate().evaluate(
        market, now_ns=DUE,
        prediction=_prediction(DUE, probability=0.1, confidence=0.8),
    )
    # Simulate a new process. The existing decision is never replaced.
    assert FinalForecastWriter(tmp_path / "bootstrap").save(
        alternative, market_close_ts_ns=CLOSE,
    ) is None
    assert json.loads(path.read_text(encoding="utf-8"))["forecast"] == "ABOVE"


def test_final_forecast_writer_disallows_raw_data_paths_and_path_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="Phase 1"):
        FinalForecastWriter(tmp_path / "data" / "raw")

    market = _market(ticker="../../WRONG")
    record = FinalForecastGate().evaluate(
        market, now_ns=DUE,
        prediction=_prediction(DUE, ticker="../../WRONG"),
    )
    with pytest.raises(ValueError, match="ticker"):
        FinalForecastWriter(tmp_path / "bootstrap").save(record, market_close_ts_ns=CLOSE)


def test_locked_forecast_preserves_checkpoint_kalshi_bid_ask_for_future_baselines(tmp_path):
    """Never reconstruct a market baseline from post-settlement prices."""
    from kalshi_edge.bootstrap.final_history import read_final_history
    market = _market()
    prediction = _prediction(DUE).model_copy(update={
        "kalshi_yes_bid": 0.71, "kalshi_yes_ask": 0.73,
    })
    locked = FinalForecastGate().evaluate(market, now_ns=DUE, prediction=prediction)
    assert locked.kalshi_yes_bid == 0.71
    assert locked.kalshi_yes_ask == 0.73
    writer = FinalForecastWriter(tmp_path / "bootstrap")
    assert writer.save(locked, market_close_ts_ns=CLOSE) is not None
    saved = read_final_history(tmp_path / "bootstrap")["forecasts"][0]
    assert saved["kalshi_yes_bid"] == 0.71
    assert saved["kalshi_yes_ask"] == 0.73


def test_reference_quote_is_not_fabricated_when_prediction_has_no_valid_two_sided_book():
    market = _market()
    blank = FinalForecastGate().evaluate(market, now_ns=DUE,
                                          prediction=_prediction(DUE))
    assert blank.kalshi_yes_bid is None
    assert blank.kalshi_yes_ask is None
    inverted = _prediction(DUE).model_copy(update={
        "kalshi_yes_bid": 0.80, "kalshi_yes_ask": 0.70,
    })
    locked = FinalForecastGate().evaluate(market, now_ns=DUE, prediction=inverted)
    assert locked.forecast == "ABOVE"
    assert locked.kalshi_yes_bid is None
    assert locked.kalshi_yes_ask is None


def test_legacy_final_forecast_json_without_market_reference_still_parses():
    from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord
    record = FinalForecastGate().evaluate(
        _market(), now_ns=DUE, prediction=_prediction(DUE)
    ).model_dump(mode="json")
    record.pop("kalshi_yes_bid", None)
    record.pop("kalshi_yes_ask", None)
    old = FinalForecastRecord.model_validate(record)
    assert old.kalshi_yes_bid is None
    assert old.kalshi_yes_ask is None
