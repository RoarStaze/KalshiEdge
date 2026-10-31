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


def test_threshold_comparison_uses_live_confidence_not_raw_side_probability():
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

    at69 = next(x for x in report["thresholds"] if x["minimum_confidence"] == .69)
    assert at69["minimum_probability"] == .845
    assert at69["predictions"] == 2
    assert at69["correct"] == 1
    assert at69["coverage"] == .50

    at80 = next(x for x in report["thresholds"] if x["minimum_confidence"] == .80)
    assert at80["minimum_probability"] == .90
    assert at80["predictions"] == 1
    assert at80["correct"] == 1

    at90 = next(x for x in report["thresholds"] if x["minimum_confidence"] == .90)
    assert at90["minimum_probability"] == .95
    assert at90["predictions"] == 0
    assert at90["accuracy"] is None
    assert report["brier_all_eligible"] is not None


def test_missing_settlements_do_not_create_accuracy():
    report = compare_thresholds([
        row(None, "ABOVE"),
        row(.9, None, settled=False),
    ])
    assert report["eligible_markets"] == 0
    assert report["thresholds"][0]["accuracy"] is None
    assert report["thresholds"][0]["coverage"] is None


def test_skip_due_to_feed_quality_is_never_reinterpreted():
    report = compare_thresholds([
        row(.999, "ABOVE", forecast="SKIP", reason="STALE_FEED"),
        row(.91, "ABOVE"),
    ])
    assert report["eligible_markets"] == 1
    assert report["thresholds"][0]["predictions"] == 1


def test_numerically_valid_probabilities_no_guarantee_model_confidence_is_calibrated():
    report = compare_thresholds([row(.845, "BELOW")])
    selected = next(x for x in report["thresholds"] if x["minimum_confidence"] == .69)
    assert selected["predictions"] == 1
    assert selected["accuracy"] == 0.0


def test_bogus_probabilities_or_invalid_probability_sum_excluded():
    records = [
        row(.9, "ABOVE"),
        row(float("nan"), "ABOVE"),
        {**row(.80, "ABOVE"), "p_no": .80},
        {**row(.78, "ABOVE"), "p_yes": True},
    ]
    result = compare_thresholds(records)
    assert result["eligible_markets"] == 1
