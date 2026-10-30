import pytest

from kalshi_edge.bootstrap.shadow_comparison import compare_shadow

NS = 1_000_000_000
T = 1_800_000_000_000_000_000


def forecast(ticker, ts, yes, actual, side="ABOVE", reason=None):
    return {
        "market_ticker": ticker,
        "prediction_ts_ns": ts,
        "scheduled_ts_ns": ts,
        "p_yes": yes,
        "p_no": 1 - yes,
        "forecast": side,
        "reason": reason,
        "actual_side": actual,
        "model_hash": "abc",
        "model_version": "bootstrap-hybrid-v5",
    }


def prediction(ticker, ts, yes, shadow, *, healthy=True,
               bt_offset=NS, bn_offset=NS, model_hash="abc"):
    return {
        "market_ticker": ticker,
        "prediction_ts_ns": ts,
        "seconds_remaining": 240.0,
        "p_yes": yes,
        "p_no": 1 - yes,
        "brti_shadow_probability": shadow,
        "brti_source_ts_ns": ts - bt_offset,
        "binance_source_ts_ns": ts - bn_offset,
        "brti_binance_gap_bps": -13.1,
        "status": "OK",
        "feed_quality": {"healthy": healthy},
        "model_hash": model_hash,
        "model_version": "bootstrap-hybrid-v5",
    }


def test_paired_metrics_and_old_forecast_exclusions():
    forecasts = [
        forecast("A", T, .7, "ABOVE"),
        forecast("B", T + 1, .3, "BELOW",
                 "SKIP", "CONFIDENCE_BELOW_69_PERCENT"),
        forecast("C", T + 2, .9, "ABOVE",
                 "SKIP", "STALE_BINANCE"),
        forecast("D", T + 3, .4, "BELOW"),
    ]
    predictions = [
        prediction("A", T, .7, .9),
        prediction("B", T + 1, .3, .8),
        prediction("C", T + 2, .9, .8),
        prediction("D", T + 3, .4, None),
    ]

    report = compare_shadow(forecasts, predictions)

    assert report["settled_forecasts"] == 4
    assert report["excluded_nonmodel_skips"] == 1
    assert report["without_stage3_shadow_fields"] == 1
    assert report["paired_markets"] == 2
    assert report["v5"]["accuracy"] == 1
    assert report["v6_shadow"]["accuracy"] == .5
    assert report["v5"]["brier"] == pytest.approx(.09)
    assert report["v6_shadow"]["brier"] == pytest.approx(.325)
    assert report["v5_only_correct"] == 1


def test_duplicates_stale_data_and_wrong_hash_are_rejected():
    forecasts = [
        forecast("A", T, .7, "ABOVE"),
        forecast("B", T + 1, .7, "ABOVE"),
        forecast("C", T + 2, .7, "ABOVE"),
    ]
    predictions = [
        prediction("A", T, .7, .8),
        prediction("A", T, .7, .8),
        prediction("B", T + 1, .7, .8, bt_offset=6 * NS),
        prediction("C", T + 2, .7, .8, model_hash="different"),
    ]

    report = compare_shadow(forecasts, predictions)

    assert report["duplicate_exact_predictions"] == 1
    assert report["rejected_unhealthy_or_inconsistent"] == 2
    assert report["paired_markets"] == 0
    assert report["status"] == "AWAITING_NEW_SHADOW_SETTLEMENTS"
    assert report["v5"]["accuracy"] is None
