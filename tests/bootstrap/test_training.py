from kalshi_edge.bootstrap import training
from kalshi_edge.bootstrap.types import FeatureRow


def _row(features: dict[str, float]) -> FeatureRow:
    return FeatureRow(
        market_ticker="KXBTC15M-TEST",
        market_date="2026-08-31",
        split_group_id="KXBTC15M-TEST",
        checkpoint_ts_ns=1_800_000_000_000_000_000,
        label_yes=1,
        features=features,
        source_max_ts_ns={"binance": 1_800_000_000_000_000_000},
    )


def test_kalshi_prior_uses_actual_feature_builder_availability_names() -> None:
    quote = _row(
        {
            "kalshi_quote_available": 1.0,
            "kalshi_mid": 0.63,
            "kalshi_trade_available": 1.0,
            "kalshi_last_trade_yes": 0.59,
        }
    )
    trade_only = _row(
        {
            "kalshi_quote_available": 0.0,
            "kalshi_mid": 0.0,
            "kalshi_trade_available": 1.0,
            "kalshi_last_trade_yes": 0.59,
        }
    )

    assert training._kalshi_prior(quote) == 0.63
    assert training._kalshi_prior(trade_only) == 0.59
    assert "kalshi_quote_available" in training.PRIOR_FEATURES
    assert "kalshi_trade_available" in training.PRIOR_FEATURES


def test_historical_evaluation_accepts_every_causal_checkpoint_at_or_above_60_seconds() -> None:
    assert training.is_evaluation_row(_row({"seconds_remaining": 780.0})) is True
    assert training.is_evaluation_row(_row({"seconds_remaining": 61.0})) is True
    assert training.is_evaluation_row(_row({"seconds_remaining": 60.0})) is True
    assert training.is_evaluation_row(_row({"seconds_remaining": 59.999})) is False
    assert training.is_evaluation_row(_row({"seconds_remaining": 30.0})) is False
