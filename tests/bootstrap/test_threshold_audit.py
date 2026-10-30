from kalshi_edge.bootstrap.threshold_audit import compare_thresholds


def row(p_yes, actual, *, forecast="ABOVE", reason=None, settled=True):
    return {
        "forecast": forecast,
        "reason": reason,
        "p_yes": p_yes,
        "p_no": None if p_yes is None else 1 - p_yes,
        "actual_side": actual,
        "settlement_status": "SETTLED" if settled else "PENDING",
        "model_version": "bootstrap-hybrid-v5",
    }


def test_threshold_comparison_and_skipped_forecasts():
    rows = [
        row(.90, "ABOVE"),
        row(.88, "BELOW"),
        row(.30, "BELOW", forecast="SKIP",
            reason="CONFIDENCE_BELOW_69_PERCENT"),
        row(.40, "ABOVE", forecast="SKIP",
            reason="CONFIDENCE_BELOW_69_PERCENT"),
        row(.99, "ABOVE", forecast="SKIP",
            reason="STALE_BINANCE"),
        row(.99, None, settled=False),
    ]

    report = compare_thresholds(rows)

    assert report["settled_markets"] == 5
    assert report["eligible_markets"] == 4
    assert report["excluded_markets"] == 1

    at69 = next(
        x for x in report["thresholds"]
        if x["minimum_probability"] == .69
    )
    assert at69["predictions"] == 3
    assert at69["correct"] == 2
    assert at69["accuracy"] == 2 / 3
    assert at69["coverage"] == .75

    at845 = next(
        x for x in report["thresholds"]
        if x["minimum_probability"] == .845
    )
    assert at845["predictions"] == 2
    assert at845["correct"] == 1
    assert at845["coverage"] == .5

    at95 = next(
        x for x in report["thresholds"]
        if x["minimum_probability"] == .95
    )
    assert at95["predictions"] == 0
    assert at95["accuracy"] is None


def test_missing_settlements_do_not_create_accuracy():
    report = compare_thresholds([
        row(None, "ABOVE"),
        row(.9, None, settled=False),
    ])

    assert report["eligible_markets"] == 0
    assert report["thresholds"][0]["accuracy"] is None
    assert report["thresholds"][0]["coverage"] is None
