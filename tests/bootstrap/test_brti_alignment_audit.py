from kalshi_edge.bootstrap.brti_alignment_audit import compare_brti_alignment


def forecast(ticker, ts, actual, probability, side="ABOVE", reason=None):
    return {
        "market_ticker": ticker,
        "prediction_ts_ns": ts,
        "strike": 100,
        "p_yes": probability,
        "p_no": 1 - probability,
        "actual_side": actual,
        "forecast": side,
        "reason": reason,
        "model_hash": "sealed",
    }


def prediction(ticker, ts, probability, brti, btc, healthy=True):
    return {
        "market_ticker": ticker,
        "prediction_ts_ns": ts,
        "strike": 100,
        "p_yes": probability,
        "p_no": 1 - probability,
        "brti_value": brti,
        "btc_reference": btc,
        "model_hash": "sealed",
        "feed_quality": {"healthy": healthy},
        "status": "OK",
    }


def test_exact_checkpoint_and_reference_comparison():
    forecasts = [
        forecast("A", 120, "ABOVE", .8),
        forecast("B", 220, "BELOW", .35, "SKIP",
                 "CONFIDENCE_BELOW_69_PERCENT"),
        forecast("C", 320, "BELOW", .8),
    ]
    predictions = [
        prediction("A", 120, .8, 102, 99),
        prediction("A", 121, .1, 99, 110),
        prediction("B", 220, .35, 99, 101),
    ]

    result = compare_brti_alignment(forecasts, predictions)

    assert result["settled_forecasts"] == 3
    assert result["matched_healthy_checkpoints"] == 2
    assert result["missing_exact_checkpoint_records"] == 1
    assert result["reference_direction_disagreements"] == 2
    assert result["brti_checkpoint_direction_accuracy"] == 1
    assert result["binance_checkpoint_direction_accuracy"] == 0
    assert result["model_direction_accuracy"] == 1


def test_duplicate_unhealthy_and_missing_data_are_rejected():
    forecasts = [
        forecast("A", 120, "ABOVE", .8),
        forecast("B", 220, "BELOW", .4),
        forecast("C", 320, "ABOVE", .95, "SKIP",
                 "STALE_BINANCE"),
    ]
    predictions = [
        prediction("A", 120, .8, 105, 99),
        prediction("A", 120, .8, 105, 99),
        prediction("B", 220, .4, 99, 101, healthy=False),
        prediction("C", 320, .95, 100, 90),
    ]

    result = compare_brti_alignment(forecasts, predictions)

    assert result["matched_healthy_checkpoints"] == 0
    assert result["duplicate_exact_checkpoint_records"] == 1
    assert result["excluded_unhealthy_or_inconsistent"] == 2
    assert result["model_direction_accuracy"] is None
