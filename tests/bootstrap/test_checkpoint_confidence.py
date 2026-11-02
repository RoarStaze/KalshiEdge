"""One checkpoint is NOT another: horizon-specific live forecast diagnostics."""
import math

import pytest

from kalshi_edge.bootstrap.checkpoint_confidence import evaluate_checkpoint_confidence


def record(ticker, *, horizon=480, p=.9, actual="ABOVE",
           decision="ABOVE", reason=None, settled=True, model="v5",
           bid=.55, ask=.65):
    return {
        "market_ticker": ticker,
        "market_close": "2026-11-01T01:00:00+00:00",
        "checkpoint_seconds": horizon,
        "settlement_status": "SETTLED" if settled else "PENDING",
        "actual_side": actual if settled else None,
        "forecast": decision,
        "reason": reason,
        "p_yes": p,
        "p_no": None if p is None else 1-p,
        "confidence": None if p is None else 2*abs(p-.5),
        "kalshi_yes_bid": bid, "kalshi_yes_ask": ask,
        "model_version": model,
    }


def test_old_four_minute_success_does_not_inflate_eight_minute_evidence():
    rows = [record(f"old-{i}", horizon=240, p=.99) for i in range(20)]
    rows += [record("new-1", horizon=480, p=.80, actual="BELOW",
                    decision="SKIP", reason="CONFIDENCE_BELOW_69_PERCENT")]
    result = evaluate_checkpoint_confidence(rows, min_sample=10)
    old = result["by_checkpoint_seconds"]["240"]
    current = result["by_checkpoint_seconds"]["480"]
    assert result["active_checkpoint_seconds"] == 480
    assert old["settled_eligible_markets"] == 20
    assert old["reliability"]["metrics"]["accuracy"] == 1.0
    assert current["settled_eligible_markets"] == 1
    assert current["reliability"]["metrics"]["accuracy"] == 0.0
    assert current["reliability"]["has_sufficient_evidence"] is False
    assert current["reliability"]["market_midpoint_comparison"]["matched_markets"] == 1
    assert result["active_policy_evidence"]["markets_scored"] == 1
    assert result["warning"].startswith("Research-only")


def test_skips_count_even_when_not_settled_but_cannot_be_scored_as_losses():
    rows = [
        record("good", p=.9, settled=True),
        record("low", p=.80, decision="SKIP",
               reason="CONFIDENCE_BELOW_69_PERCENT", settled=True),
        record("feed", p=None, decision="SKIP",
               reason="STALE_KALSHI_BOOK", settled=True, bid=None, ask=None),
        record("late", p=None, decision="SKIP",
               reason="MISSED_480S_CHECKPOINT", settled=False, bid=None, ask=None),
        record("pending", p=.88, settled=False),
    ]
    report = evaluate_checkpoint_confidence(rows, min_sample=5)["by_checkpoint_seconds"]["480"]
    assert report["total_forecast_records"] == 5
    assert report["directional_forecasts"] == 2
    assert report["low_confidence_skips"] == 1
    assert report["feed_or_timing_skips"] == 2
    assert report["pending_settlements"] == 2
    assert report["recorded_signal_fraction"] == 2/5
    assert report["settled_eligible_markets"] == 2
    assert report["reliability"]["markets_scored"] == 2
    assert report["skip_reasons"]["MISSED_480S_CHECKPOINT"] == 1


def test_low_confidence_skip_is_probability_observation_not_auto_trade():
    row = record("skip", p=.7998, decision="SKIP",
                 reason="CONFIDENCE_BELOW_69_PERCENT")
    report = evaluate_checkpoint_confidence([row])["by_checkpoint_seconds"]["480"]
    assert report["low_confidence_skips"] == 1
    assert report["reliability"]["markets_scored"] == 1
    assert report["reliability"]["confidence_groups"][1]["markets"] == 0
    assert report["minimum_model_side_probability_for_trading"] == .845


def test_missing_horizon_in_legacy_history_defaults_only_to_240():
    old = record("old", horizon=240)
    old.pop("checkpoint_seconds")
    current = record("current", horizon=480)
    report = evaluate_checkpoint_confidence([old,current])
    assert report["by_checkpoint_seconds"]["240"]["total_forecast_records"] == 1
    assert report["by_checkpoint_seconds"]["480"]["total_forecast_records"] == 1


@pytest.mark.parametrize("bad", [None,0,-1,True, 480.0, "480", 600])
def test_malformed_explicit_horizon_does_not_contaminate_live_group(bad):
    report = evaluate_checkpoint_confidence([record("bad", horizon=bad)])
    assert report["invalid_checkpoint_records"] == 1
    assert report["active_policy_evidence"]["markets_scored"] == 0


def test_duplicate_ticker_all_removed_from_480_reliability():
    rows = [record("same"),record("same",p=.3,actual="BELOW",decision="BELOW")]
    r = evaluate_checkpoint_confidence(rows)
    assert r["by_checkpoint_seconds"]["480"]["reliability"]["markets_scored"] == 0
    assert r["by_checkpoint_seconds"]["480"]["reliability"]["duplicates_excluded"] == 2


def test_invalid_probabilities_not_counted_as_calibrated():
    rows = [record("bad",p=math.nan), record("good",p=.91)]
    r = evaluate_checkpoint_confidence(rows)["by_checkpoint_seconds"]["480"]
    assert r["total_forecast_records"] == 2
    assert r["settled_eligible_markets"] == 1


def test_empty_history_and_invalid_min_sample():
    report=evaluate_checkpoint_confidence([])
    assert report["active_policy_evidence"]["markets_scored"] == 0
    assert report["by_checkpoint_seconds"]["480"]["recorded_signal_fraction"] is None
    assert report["by_checkpoint_seconds"]["480"]["reliability"]["metrics"] is None
    for bad in [0,-1,True,1.5]:
        with pytest.raises(ValueError):
            evaluate_checkpoint_confidence([],min_sample=bad)


def test_detect_model_confidence_disagreement_without_rewriting_probability():
    row = record("bad",p=.8)
    row["confidence"]=.97
    result = evaluate_checkpoint_confidence([row])["by_checkpoint_seconds"]["480"]
    assert result["inconsistent_confidence_records"] == 1
    assert result["reliability"]["metrics"]["markets"] == 1
    assert result["warning"].startswith("Research-only")
