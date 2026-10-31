"""Read-only reliability monitoring of actual settled BTC-15m forecasts."""
import math

from kalshi_edge.bootstrap.forward_monitor import evaluate_forward_reliability


def record(ticker, p_yes, actual, *, model="v5", settled=True,
           decision="ABOVE", reason=None, market_close="2026-10-31T12:00:00+00:00"):
    return {
        "market_ticker": ticker,
        "market_close": market_close,
        "settlement_status": "SETTLED" if settled else "PENDING",
        "forecast": decision,
        "reason": reason,
        "p_yes": p_yes,
        "p_no": None if p_yes is None else 1 - p_yes,
        "actual_side": actual,
        "model_version": model,
    }


def test_counts_only_unique_settled_valid_markets_and_exposes_scoring():
    rows = [
        record("market-a", .90, "ABOVE"),
        record("market-a", .90, "ABOVE"),
        record("market-b", .10, "BELOW"),
        record("market-e", .90, "ABOVE"),
        record("market-c", .99, "ABOVE", settled=False),
        record("market-d", .98, "BELOW", decision="SKIP", reason="STALE_FEED"),
    ]
    report = evaluate_forward_reliability(rows, min_sample=3)
    assert report["markets_scored"] == 2
    assert report["duplicates_excluded"] == 2  # both duplicate rows excluded
    assert report["pending_excluded"] == 1
    assert report["ineligible_excluded"] == 1
    assert math.isclose(report["metrics"]["brier"], .01, abs_tol=1e-9)
    assert math.isclose(report["metrics"]["accuracy"], 1.0)
    assert report["readiness"] == "INSUFFICIENT_SAMPLE"
    assert report["minimum_required_markets"] == 3


def test_low_confidence_skip_can_be_scored_for_calibration_if_auditable():
    rows = [
        record("market-a", .60, "ABOVE", decision="SKIP",
               reason="CONFIDENCE_BELOW_69_PERCENT"),
        record("market-b", .50, "BELOW", decision="SKIP",
               reason="CONFIDENCE_BELOW_69_PERCENT"),
    ]
    report = evaluate_forward_reliability(rows, min_sample=2)
    assert report["markets_scored"] == 2
    assert report["readiness"] == "DESCRIPTIVE_ONLY"
    assert report["confidence_groups"][0]["markets"] == 2
    assert report["confidence_groups"][0]["wins"] == 1


def test_confidence_bins_use_true_side_probability_and_report_wilson_bounds():
    rows = [
        record("a", .95, "ABOVE"),
        record("b", .05, "BELOW"),
        record("c", .96, "BELOW"),
        record("d", .12, "ABOVE"),
    ]
    report = evaluate_forward_reliability(rows, min_sample=4)
    band = next(b for b in report["confidence_groups"]
                if b["minimum_confidence"] == .80)
    assert band["markets"] == 3
    assert band["wins"] == 2
    assert band["observed_accuracy"] == 2 / 3
    assert 0 < band["wilson_lower_95"] < band["observed_accuracy"]
    assert band["mean_model_win_probability"] > .90


def test_sorted_last_n_windows_by_market_close_not_input_order():
    rows = [
        record("new", .99, "BELOW", market_close="2026-10-31T12:15:00+00:00"),
        record("old", .90, "ABOVE", market_close="2026-10-31T12:00:00+00:00"),
        record("middle", .85, "ABOVE", market_close="2026-10-31T12:05:00+00:00"),
    ]
    report = evaluate_forward_reliability(rows, min_sample=2, windows=(2,))
    assert report["windows"][0]["markets"] == 2
    assert report["windows"][0]["accuracy"] == .5
    assert report["windows"][0]["first_market"] == "middle"
    assert report["windows"][0]["last_market"] == "new"


def test_separate_model_version_scores_do_not_mix_predictions():
    rows = [
        record("a", .95, "ABOVE", model="v5"),
        record("b", .95, "BELOW", model="v6"),
    ]
    report = evaluate_forward_reliability(rows, min_sample=2)
    assert report["by_model"]["v5"]["markets"] == 1
    assert report["by_model"]["v5"]["accuracy"] == 1
    assert report["by_model"]["v6"]["markets"] == 1
    assert report["by_model"]["v6"]["accuracy"] == 0


def test_invalid_finite_probability_and_naive_skip_excluded():
    rows = [
        record("a", math.nan, "ABOVE"),
        record("b", .9, "ABOVE", decision="SKIP", reason="MISSING_FEED"),
        record("c", .8, "ABOVE", decision="SKIP", reason=None),
        record("d", .9, "ABOVE", market_close="bad"),
        record("e", .9, "ABOVE"),
    ]
    rows[2]["p_no"] = .8
    report = evaluate_forward_reliability(rows, min_sample=2)
    assert report["markets_scored"] == 1
    assert report["ineligible_excluded"] == 4
    assert not report["has_sufficient_evidence"]


def test_same_market_repeated_in_different_versions_is_not_double_counted():
    rows = [
        record("same", .8, "ABOVE", model="v5"),
        record("same", .7, "BELOW", model="v6"),
    ]
    report = evaluate_forward_reliability(rows, min_sample=2)
    assert report["markets_scored"] == 0
    assert report["duplicates_excluded"] == 2
    assert report["metrics"] is None


def test_metrics_not_computed_for_empty_or_all_pending_history():
    report = evaluate_forward_reliability([record("a", .9, "ABOVE", settled=False)])
    assert report["metrics"] is None
    assert report["markets_scored"] == 0
    assert report["readiness"] == "INSUFFICIENT_SAMPLE"
