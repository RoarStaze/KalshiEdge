import pytest
from pydantic import ValidationError

from kalshi_edge.bootstrap.types import FeedQuality, FeatureRow, MarketLabel, PredictionRecord


def test_bootstrap_contracts_are_frozen_and_probability_safe() -> None:
    label = MarketLabel(
        ticker="KXBTC15M-X",
        strike=100000.0,
        result="yes",
        settlement_value=100100.0,
        open_ts_ns=1,
        close_ts_ns=2,
        settlement_ts_ns=3,
    )
    row = FeatureRow(
        market_ticker=label.ticker,
        checkpoint_ts_ns=1,
        label_yes=1,
        features={"seconds_remaining": 60.0},
        source_max_ts_ns={"btc": 1},
    )
    quality = FeedQuality(healthy=True)
    prediction = PredictionRecord(
        prediction_ts_ns=1,
        market_ticker=label.ticker,
        status="OK",
        p_yes=0.6,
        p_no=0.4,
        predicted_side="ABOVE",
        feed_quality=quality,
        model_hash="abc",
    )

    assert row.label_yes == 1
    assert prediction.p_yes + prediction.p_no == pytest.approx(1.0)
    with pytest.raises(ValidationError):
        label.strike = 1.0


def test_prediction_rejects_invalid_probability() -> None:
    with pytest.raises(ValidationError):
        PredictionRecord(
            prediction_ts_ns=1,
            market_ticker="KXBTC15M-X",
            status="OK",
            p_yes=1.1,
            p_no=-0.1,
            predicted_side="ABOVE",
            feed_quality=FeedQuality(healthy=True),
            model_hash="abc",
        )


def test_no_prediction_allows_missing_probabilities_with_reason() -> None:
    record = PredictionRecord(
        prediction_ts_ns=1,
        market_ticker=None,
        status="NO_PREDICTION",
        p_yes=None,
        p_no=None,
        predicted_side=None,
        feed_quality=FeedQuality(healthy=False, reasons=("stale_btri",)),
        model_hash=None,
        reason="stale_btri",
    )
    assert record.status == "NO_PREDICTION"
    assert record.reason == "stale_btri"
