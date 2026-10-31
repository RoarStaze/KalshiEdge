"""Paired day-cluster score intervals are descriptive research, not live signals."""
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.bootstrap.paired_skill import paired_market_skill_interval


def records(days=8, per_day=10, *, mixed=False):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    for day in range(days):
        for minute in range(per_day):
            time = start + timedelta(days=day, minutes=minute)
            yes = (minute % 2 == 0)
            ai = .90 if yes else .10
            market = .50
            if mixed and day % 2:
                ai, market = .50, ai
            rows.append((time, ai, market, int(yes)))
    return rows


def test_scarce_markets_are_not_presented_as_significant_skill():
    report = paired_market_skill_interval(records(days=4, per_day=10))
    assert report["status"] == "INSUFFICIENT_SAMPLE"
    assert report["paired_markets"] == 40
    assert report["distinct_days"] == 4
    assert report["brier_skill_ci_95"] is None


def test_sufficient_identical_positive_days_yield_positive_paired_interval():
    report = paired_market_skill_interval(records(), resamples=200, seed=31)
    assert report["status"] == "DESCRIPTIVE_DAY_CLUSTER_BOOTSTRAP"
    assert report["paired_markets"] == 80
    assert report["distinct_days"] == 8
    assert report["brier_skill_ci_95"]["low"] > 0
    assert report["brier_skill_ci_95"]["high"] > 0
    assert report["log_loss_skill_ci_95"]["low"] > 0
    assert report["independent_validation"] is False


def test_mixed_day_regimes_do_not_claim_an_unequivocal_advantage():
    report = paired_market_skill_interval(records(mixed=True), resamples=500, seed=42)
    bounds = report["brier_skill_ci_95"]
    assert bounds["low"] < 0 < bounds["high"]


def test_reproducible_seed_and_nonparametric_day_cluster():
    a = paired_market_skill_interval(records(mixed=True), resamples=100, seed=17)
    b = paired_market_skill_interval(records(mixed=True), resamples=100, seed=17)
    assert a == b
    assert a["resamples"] == 100


def test_rejects_invalid_probabilities_labels_and_naive_datetimes():
    bad = records()
    bad[0] = (bad[0][0], float("nan"), .4, 1)
    with pytest.raises(ValueError):
        paired_market_skill_interval(bad)
    bad = records()
    bad[0] = (bad[0][0].replace(tzinfo=None), .8, .5, 1)
    with pytest.raises(ValueError):
        paired_market_skill_interval(bad)
    bad = records()
    bad[0] = (bad[0][0], .8, .5, True)
    with pytest.raises(ValueError):
        paired_market_skill_interval(bad)


@pytest.mark.parametrize("resamples,min_markets,min_days", [
    (0, 50, 7), (100, 0, 7), (100, 50, 0), (10001, 50, 7)])
def test_invalid_bootstrap_parameters_rejected(resamples, min_markets, min_days):
    with pytest.raises(ValueError):
        paired_market_skill_interval(
            records(), resamples=resamples,
            min_markets=min_markets, min_days=min_days)


def test_forward_monitor_includes_but_does_not_act_on_uncertainty():
    from kalshi_edge.bootstrap.forward_monitor import evaluate_forward_reliability
    rows = []
    for i, (ts, ai, midpoint, y) in enumerate(records()):
        rows.append({
            "market_ticker": f"KXBTC15M-TEST-{i}",
            "market_close": ts.isoformat(),
            "p_yes": ai, "p_no": 1-ai,
            "forecast": "ABOVE" if ai >= .5 else "BELOW",
            "settlement_status": "SETTLED",
            "actual_side": "ABOVE" if y else "BELOW",
            "model_version": "v5",
            "kalshi_yes_bid": midpoint-.05,
            "kalshi_yes_ask": midpoint+.05,
        })
    report = evaluate_forward_reliability(rows)
    interval = report["market_midpoint_comparison"]["paired_uncertainty"]
    assert interval["status"] == "DESCRIPTIVE_DAY_CLUSTER_BOOTSTRAP"
    assert interval["brier_skill_ci_95"]["low"] > 0
    assert report["independent_validation"] is False
