"""Research-only chronological evaluation of *observable* quote flips.

Success means a later 50-cent quote crossover, not a profitable order.
"""
from __future__ import annotations

import json

from kalshi_edge.bootstrap.flip_replay import evaluate_prediction_stream

NS = 1_000_000_000
START = 1_800_100_000_000_000_000
TICKER = "KXBTC15M-REPLAY"


def write_stream(path, *, later_cross=True, max_i=60, gap=False):
    with path.open("w", encoding="utf-8") as out:
        for i in range(max_i):
            j=max(0,i-25)
            p=.82-.03*j if i<=34 else .53
            mid=.78-.02*j if i<=34 else (.45 if later_cross else .60)
            if i>=40:
                p=.33
            elif i>34:
                p=.49 if later_cross else .53
            if gap and 37 <= i <= 42:
                continue
            record={
                "prediction_ts_ns":START+i*NS,
                "market_ticker":TICKER,
                "status":"OK","feed_quality":{"healthy":True},
                "model_hash":"a"*64,
                "p_yes":p,"p_no":1-p,
                "predicted_side":"ABOVE" if p>=.5 else "BELOW",
                "kalshi_yes_bid":mid-.01,"kalshi_yes_ask":mid+.01,
                "strike":64000,"seconds_remaining":900-i,
            }
            out.write(json.dumps(record)+"\n")


def test_replay_scores_first_early_warning_only_against_future_quote_cross(tmp_path):
    file=tmp_path/"predictions.jsonl"
    write_stream(file)
    result=evaluate_prediction_stream(file,horizon_seconds=15)
    assert result["analysis_status"] == "DESCRIPTIVE_ONLY"
    assert result["label_type"] == "FUTURE_KALSHI_QUOTE_MID_CROSS_50C"
    assert result["alerts"] == 1
    assert result["resolved"] == 1
    assert result["hits"] == 1
    assert result["misses"] == 0
    assert result["precision"] == 1.
    assert result["profitable_trade_probability"] is None
    assert 0 <= result["wilson_lower_95"] <= result["wilson_upper_95"] <= 1


def test_replay_can_count_false_alarms_and_never_use_unobserved_future(tmp_path):
    file=tmp_path/"predictions.jsonl"
    write_stream(file,later_cross=False)
    result=evaluate_prediction_stream(file,horizon_seconds=15)
    assert result["resolved"] == 1
    assert result["misses"] == 1
    assert result["precision"] == 0.
    write_stream(file,max_i=35)
    pending=evaluate_prediction_stream(file,horizon_seconds=15)
    assert pending["resolved"] == 0
    assert pending["unresolved"] >= 1


def test_replay_with_observation_gap_disqualifies_expiry(tmp_path):
    file=tmp_path/"predictions.jsonl"
    write_stream(file,gap=True)
    result=evaluate_prediction_stream(file,horizon_seconds=15)
    assert result["resolved"] == 0
    assert result["unresolved"] >= 1


def test_replay_never_writes_orders_or_mutates_input(tmp_path):
    file=tmp_path/"predictions.jsonl"
    write_stream(file)
    before=file.read_bytes()
    result=evaluate_prediction_stream(file)
    assert file.read_bytes()==before
    assert result["live_orders_enabled"] is False
