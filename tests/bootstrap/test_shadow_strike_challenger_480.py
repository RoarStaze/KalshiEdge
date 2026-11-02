"""Research-only strike/BRTI/market challenger; never deploy on historical gains."""
from datetime import datetime, timezone
import math
import pytest

from kalshi_edge.bootstrap.shadow_strike_challenger_480 import evaluate_strike_challenger

NS = 1_000_000_000
BASE = 1_900_000_000 * NS
HASH = "stable-promoted-model"


def row(i, *, model_hash=HASH, brti=None, yes=None, p=None, quote=(.48, .52)):
    due=BASE+i*900*NS
    up=i%2==0
    brti = (105_000 if up else 95_000) if brti is None else brti
    # Most incumbent forecasts below the confidence cutoff; some strongly
    # confident incumbent mistakes make correctness and coverage testable.
    if p is None:
        p = (.90 if i%10==0 else .55) if up else (.10 if i%10==1 else .45)
        if i%20==0:
            p = .10 if up else .90
    confidence=2*abs(p-.5)
    return {
        "source":"ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS",
        "market_ticker":f"KXBTC15M-TEST-{i}",
        "checkpoint_seconds":480,
        "scheduled_checkpoint_ts_ns":due,
        "recorded_prediction_ts_ns":due+NS,
        "market_close":datetime.fromtimestamp((due+480*NS)/NS,timezone.utc).isoformat(),
        "model_hash":model_hash,"model_version":"v5",
        "settlement_status":"SETTLED",
        "actual_side":"ABOVE" if up else "BELOW",
        "forecast":"SKIP" if confidence<.69 else ("ABOVE" if p>=.5 else "BELOW"),
        "reason":"CONFIDENCE_BELOW_69_PERCENT" if confidence<.69 else None,
        "p_yes":p,"p_no":1-p,"confidence":confidence,
        "kalshi_yes_bid":quote[0],"kalshi_yes_ask":quote[1],
        "strike":100_000.0,"brti_value":brti,
        "brti_source_ts_ns":due,
    }


def test_predeclared_multifeature_challenger_can_improve_both_goals_on_synthetic_signal():
    rows=[row(i) for i in range(100)]
    result=evaluate_strike_challenger(list(reversed(rows)))
    assert result["status"]=="RETROSPECTIVE_SHADOW_ONLY"
    assert result["training_markets"]==60
    assert result["evaluation_markets"]==40
    assert result["train_last_market"]==rows[59]["market_ticker"]
    assert result["eval_first_market"]==rows[60]["market_ticker"]
    assert result["features"]==[
        "incumbent_log_odds", "kalshi_midpoint_log_odds", "brti_to_strike_distance_100bps"
    ]
    assert result["candidate"]["brier"] < result["incumbent"]["brier"]
    assert result["candidate"]["log_loss"] < result["incumbent"]["log_loss"]
    assert result["candidate_69_gate"]["eligible_markets"] > result["incumbent_69_gate"]["eligible_markets"]
    assert result["candidate_69_gate"]["accuracy"] >= result["incumbent_69_gate"]["accuracy"]
    assert result["joint_target_met_retrospectively"] is True
    assert result["model_promotion"] is False
    assert result["live_orders"] is False
    assert result["independent_validation"] is False


def test_insufficient_features_do_not_infer_new_confidence():
    r=evaluate_strike_challenger([row(i) for i in range(79)])
    assert r["status"]=="INSUFFICIENT_MARKETS"
    assert r["candidate"] is None
    assert r["joint_target_met_retrospectively"] is False


def test_only_one_model_hash_and_no_posthoc_selection():
    values=[row(i,model_hash="A" if i<60 else "B") for i in range(100)]
    r=evaluate_strike_challenger(values)
    assert r["status"]=="MIXED_MODEL_HASHES"
    assert r["candidate"] is None
    assert r["model_hash_counts"]=={"A":60,"B":40}


def test_exclude_all_duplicates_even_if_one_has_opposite_outcome():
    values=[row(i) for i in range(100)]
    duplicate=row(80)
    duplicate["actual_side"]="BELOW"
    r=evaluate_strike_challenger([*values,duplicate])
    assert r["duplicate_records_excluded"]==2
    assert r["eligible_markets"]==99
    assert r["evaluation_markets"]==39


@pytest.mark.parametrize("field,value",[
    ("brti_value",None),("brti_value",float("nan")),
    ("brti_value",True),("brti_value",-5),
    ("strike",0),("strike",float("inf")),
    ("brti_source_ts_ns",None),("brti_source_ts_ns",True),
    ("kalshi_yes_bid",None),("kalshi_yes_ask",float("nan")),
    ("p_yes",1.5),("checkpoint_seconds",240),
    ("settlement_status","PENDING"),("actual_side","PENDING"),
])
def test_invalid_or_missing_inputs_fail_closed(field,value):
    values=[row(i) for i in range(100)]
    values[10][field]=value
    r=evaluate_strike_challenger(values)
    assert r["eligible_markets"]==99
    assert r["invalid_or_unusable_records"]==1


def test_future_and_stale_brti_samples_are_excluded():
    values=[row(i) for i in range(100)]
    values[4]["brti_source_ts_ns"]=values[4]["recorded_prediction_ts_ns"]+NS
    values[5]["brti_source_ts_ns"]=values[5]["recorded_prediction_ts_ns"]-7*NS
    r=evaluate_strike_challenger(values)
    assert r["eligible_markets"]==98


def test_crossed_spread_not_a_calibrated_market_probability():
    values=[row(i) for i in range(100)]
    values[0]["kalshi_yes_bid"]=.95
    values[0]["kalshi_yes_ask"]=.05
    r=evaluate_strike_challenger(values)
    assert r["eligible_markets"]==99


def test_train_single_outcome_class_refuses_fitting():
    values=[row(i) for i in range(100)]
    for item in values[:60]:
        item["actual_side"]="ABOVE"
    r=evaluate_strike_challenger(values)
    assert r["status"]=="INSUFFICIENT_TRAIN_LABEL_VARIATION"
    assert r["candidate"] is None


def test_replaying_480s_snapshot_preserves_original_brti_and_strike_fields(tmp_path):
    from kalshi_edge.bootstrap.shadow_eight_minute import replay_eight_minute_history
    from kalshi_edge.bootstrap.live import LivePredictionRecord
    from kalshi_edge.bootstrap.types import FeedQuality
    close = BASE + 900 * NS
    due = close - 480 * NS
    ticker = "KXBTC15M-REPLAY"
    live = LivePredictionRecord(
        prediction_ts_ns=due, market_ticker=ticker, status="OK",
        p_yes=.9, p_no=.1, predicted_side="ABOVE", confidence=.8,
        seconds_remaining=480, model_hash="hash", model_version="v5",
        feed_quality=FeedQuality(healthy=True),
        strike=100_000.0, brti_value=100_150.0,
        brti_source_ts_ns=due,
    )
    day=datetime.fromtimestamp(due/NS,timezone.utc).date().isoformat()
    file=tmp_path/"predictions"/day/"predictions.jsonl"
    file.parent.mkdir(parents=True)
    file.write_text(live.model_dump_json()+"\\n",encoding="utf-8")
    old = {
        "market_ticker":ticker,
        "market_close":datetime.fromtimestamp(close/NS,timezone.utc).isoformat(),
        "checkpoint_seconds":240,"settlement_status":"SETTLED",
        "actual_side":"ABOVE",
    }
    result=replay_eight_minute_history(tmp_path,[old],include_replay_rows=True)
    assert len(result["replay_rows"])==1
    saved=result["replay_rows"][0]
    assert saved["strike"]==100_000
    assert saved["brti_value"]==100_150
    assert saved["brti_source_ts_ns"]==due
    assert "brti_value" not in result["reliability"]


def test_candidate_compared_on_exact_same_market_subset_without_live_mutation():
    values=[row(i) for i in range(100)]
    r=evaluate_strike_challenger(values)
    assert r["incumbent"]["markets"]==r["candidate"]["markets"]==40
    assert r["market_midpoint"]["markets"]==40
    assert r["training_precedes_evaluation"] is True
    assert r["warning"].startswith("Retrospective")
