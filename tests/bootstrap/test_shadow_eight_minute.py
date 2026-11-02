"""Retrospective 480s replay from immutable live JSONL, not a final forecast."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.bootstrap.shadow_eight_minute import replay_eight_minute_history


NS=1_000_000_000
CLOSE=1_900_000_900 * NS
DUE=CLOSE-480*NS
TICKER="KXBTC15M-SHADOWTEST"


def history(ticker=TICKER, *, close=CLOSE, horizon=240, actual="ABOVE",
            settled=True, confidence=.8):
    return {
        "market_ticker":ticker,
        "market_close":datetime.fromtimestamp(close/NS,timezone.utc).isoformat(),
        "checkpoint_seconds":horizon,
        "settlement_status":"SETTLED" if settled else "PENDING",
        "actual_side":actual if settled else None,
        "forecast":"ABOVE",
        "p_yes":.9,
        "p_no":.1,
        "confidence":confidence,
    }


def snapshot(ticker=TICKER, *, at=DUE, close=CLOSE, p=.9,
             status="OK", healthy=True, reason=None):
    return LivePredictionRecord(
        prediction_ts_ns=at, market_ticker=ticker,
        status=status,
        reason=reason if status != "OK" else None,
        feed_quality=FeedQuality(healthy=healthy),
        model_hash="hash",model_version="v5",
        p_yes=p if status=="OK" else None,
        p_no=1-p if status=="OK" else None,
        predicted_side=("ABOVE" if p>=.5 else "BELOW") if status=="OK" else None,
        confidence=2*abs(p-.5) if status=="OK" else None,
        seconds_remaining=(close-at)/NS,
        kalshi_yes_bid=.6,kalshi_yes_ask=.7,
    )


def archive(root, *records):
    from datetime import datetime, timezone
    for rec in records:
        day=datetime.fromtimestamp(rec.prediction_ts_ns/NS,timezone.utc).date().isoformat()
        path=root/"predictions"/day/"predictions.jsonl"
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("a",encoding="utf-8") as f:
            f.write(rec.model_dump_json()+"\n")


def test_legacy_actual_outcome_and_exact_480s_prediction_measured_but_not_filled(tmp_path):
    archive(tmp_path, snapshot(p=.91))
    r=replay_eight_minute_history(tmp_path,[history()])
    assert r["source"]=="ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS"
    assert r["selected_legacy_settled_markets"]==1
    assert r["markets_with_on_time_observation"]==1
    assert r["markets_without_on_time_observation"]==0
    assert r["hypothetical_480_directional_decisions"]==1
    assert r["hypothetical_480_low_confidence_skips"]==0
    assert r["reliability"]["markets_scored"]==1
    assert r["reliability"]["confidence_groups"][1]["markets"]==1
    assert r["reliability"]["market_midpoint_comparison"]["matched_markets"]==1
    assert r["live_orders_submitted"] is False
    assert r["independent_validation"] is False


def test_80_pct_probability_replays_as_skip_not_as_real_trade(tmp_path):
    archive(tmp_path,snapshot(p=.7998))
    result=replay_eight_minute_history(tmp_path,[history()])
    assert result["hypothetical_480_directional_decisions"]==0
    assert result["hypothetical_480_low_confidence_skips"]==1
    assert result["reliability"]["markets_scored"]==1
    assert result["reliability"]["confidence_groups"][1]["markets"]==0
    assert "not" in result["warning"].lower()


def test_first_observation_is_unhealthy_and_later_healthy_cannot_be_cherry_picked(tmp_path):
    archive(tmp_path,snapshot(at=DUE,status="NO_PREDICTION",
                              healthy=False,reason="STALE_KALSHI_BOOK"),
            snapshot(at=DUE+NS,p=.99))
    r=replay_eight_minute_history(tmp_path,[history()])
    assert r["markets_with_on_time_observation"]==1
    assert r["unhealthy_or_invalid_observations"]==1
    assert r["hypothetical_480_directional_decisions"]==0
    assert r["reliability"]["markets_scored"]==0
    assert r["replay_skip_reasons"]["STALE_KALSHI_BOOK"]==1


def test_only_scheduled_and_two_seconds_later_causal_ticks_allowed(tmp_path):
    archive(tmp_path, snapshot(at=DUE-NS,p=.99),
            snapshot(at=DUE+3*NS,p=.99))
    r=replay_eight_minute_history(tmp_path,[history()])
    assert r["markets_without_on_time_observation"]==1
    assert r["reliability"]["markets_scored"]==0


def test_duplicate_earliest_tick_fails_closed(tmp_path):
    archive(tmp_path,snapshot(p=.9),snapshot(p=.1))
    result=replay_eight_minute_history(tmp_path,[history()])
    assert result["ambiguous_earliest_observations"]==1
    assert result["reliability"]["markets_scored"]==0


def test_only_legacy_settled_labels_not_new_480_or_pending_or_other_ticker(tmp_path):
    archive(tmp_path,snapshot(ticker="WRONG"),snapshot())
    result=replay_eight_minute_history(tmp_path,[
        history(),history("new",horizon=480),
        history("pending",settled=False),
    ])
    assert result["selected_legacy_settled_markets"]==1
    assert result["reliability"]["markets_scored"]==1


def test_duplicate_market_labels_excluded_all_not_chosen_by_accuracy(tmp_path):
    archive(tmp_path,snapshot())
    result=replay_eight_minute_history(tmp_path,[history(),history(actual="BELOW")])
    assert result["duplicate_legacy_labels_excluded"]==2
    assert result["selected_legacy_settled_markets"]==0


def test_no_older_1hz_archives_means_no_claim_to_replayed_skill(tmp_path):
    r=replay_eight_minute_history(tmp_path,[history()])
    assert r["markets_with_on_time_observation"]==0
    assert r["reliability"]["metrics"] is None
    assert r["status"]=="NO_VALID_480S_REPLAY_EVIDENCE"


def test_multiple_dates_and_market_selection_remain_bounded(tmp_path):
    two=history("SECOND",close=CLOSE+15*60*NS)
    archive(tmp_path,snapshot(),snapshot("SECOND",at=DUE+15*60*NS,close=CLOSE+15*60*NS))
    r=replay_eight_minute_history(tmp_path,[history(),two],max_markets=1)
    assert r["selected_legacy_settled_markets"]==1
    assert r["reliability"]["markets_scored"]==1


@pytest.mark.parametrize("max_markets",[0,-1,True,1.5])
def test_invalid_replay_limit_rejected(tmp_path,max_markets):
    with pytest.raises(ValueError):
        replay_eight_minute_history(tmp_path,[history()],max_markets=max_markets)


def test_replay_exposes_unmodified_causal_rows_only_by_opt_in(tmp_path):
    archive(tmp_path, snapshot(p=.7998))
    normal = replay_eight_minute_history(tmp_path, [history()])
    assert "replay_rows" not in normal
    research = replay_eight_minute_history(
        tmp_path, [history()], include_replay_rows=True,
    )
    assert len(research["replay_rows"]) == 1
    row = research["replay_rows"][0]
    assert row["model_hash"] == "hash"
    assert row["checkpoint_seconds"] == 480
    assert row["p_yes"] == .7998
    assert row["forecast"] == "SKIP"
    assert row["source"] == "ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS"
