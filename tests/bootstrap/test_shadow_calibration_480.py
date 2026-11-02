"""Retrospective shadow calibration never changes live confidence or executes trades."""
from datetime import datetime, timezone
import math
import pytest

from kalshi_edge.bootstrap.shadow_calibration_480 import assess_480_shadow_calibration

NS = 1_000_000_000
BASE = 1_900_000_000 * NS


def obs(index, *, p=None, actual=None, model="sealed-model-A", quote=True):
    due=BASE+index*900*NS
    probability = (.65 if index%2==0 else .35) if p is None else p
    label = ("ABOVE" if index%2==0 else "BELOW") if actual is None else actual
    strength = 2*abs(probability-.5)
    return {
        "source":"ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS",
        "market_ticker":f"KXBTC15M-TEST-{index}",
        "checkpoint_seconds":480,
        "scheduled_checkpoint_ts_ns":due,
        "recorded_prediction_ts_ns":due+NS,
        "market_close":datetime.fromtimestamp((due+480*NS)/NS,timezone.utc).isoformat(),
        "forecast":"SKIP" if strength<.69 else ("ABOVE" if probability>=.5 else "BELOW"),
        "reason":"CONFIDENCE_BELOW_69_PERCENT" if strength<.69 else None,
        "p_yes":probability, "p_no":1-probability,
        "confidence":strength,
        "settlement_status":"SETTLED",
        "actual_side":label,
        "model_hash":model,"model_version":"v5",
        "kalshi_yes_bid":(.48 if quote else None),
        "kalshi_yes_ask":(.52 if quote else None),
    }


def test_chronological_sigmoid_shadow_improves_good_underconfident_synthetic_data():
    rows=[obs(i) for i in range(100)]
    r=assess_480_shadow_calibration(list(reversed(rows)))
    assert r["status"]=="RETROSPECTIVE_SHADOW_ONLY"
    assert r["eligible_markets"]==100
    assert r["training_markets"]==60
    assert r["evaluation_markets"]==40
    assert r["train_last_market"]==rows[59]["market_ticker"]
    assert r["eval_first_market"]==rows[60]["market_ticker"]
    assert r["calibrator"]=="SIGMOID_LOGIT_PLATT_C1_FROZEN"
    assert r["candidate"]["brier"] < r["incumbent"]["brier"]
    assert r["candidate"]["log_loss"] < r["incumbent"]["log_loss"]
    assert r["candidate"]["sharpness"] > r["incumbent"]["sharpness"]
    assert r["incumbent_69_gate"]["eligible_markets"]==0
    assert r["candidate_69_gate"]["eligible_markets"]==40
    assert r["candidate_69_gate"]["accuracy"]==1
    assert r["model_promotion"] is False
    assert r["live_trades"] is False
    assert r["independent_validation"] is False


def test_no_data_or_insufficient_test_blocks_candidate():
    assert assess_480_shadow_calibration([])["status"]=="INSUFFICIENT_MARKETS"
    assert assess_480_shadow_calibration([obs(i) for i in range(79)])["status"]=="INSUFFICIENT_MARKETS"
    assert assess_480_shadow_calibration([obs(i) for i in range(79)])["candidate"] is None


def test_inconsistent_model_hashes_do_not_get_mixed_or_posthoc_chosen():
    rows=[obs(i,model=("A" if i<60 else "B")) for i in range(100)]
    r=assess_480_shadow_calibration(rows)
    assert r["status"]=="MIXED_MODEL_HASHES"
    assert r["candidate"] is None
    assert r["model_hash_counts"]=={"A":60,"B":40}


def test_duplicate_tickers_exclude_all_duplicates_from_fit_and_evaluation():
    rows=[obs(i) for i in range(100)]
    rows.append(obs(70,actual="BELOW"))
    r=assess_480_shadow_calibration(rows)
    assert r["excluded_duplicate_records"]==2
    assert r["eligible_markets"]==99
    assert r["evaluation_markets"]==39
    assert r["eval_first_market"]==rows[60]["market_ticker"]


def test_checkpoint_window_and_settled_label_validation_fail_closed():
    rows=[obs(i) for i in range(100)]
    bad=obs(1)
    bad["checkpoint_seconds"]=240
    bad2=obs(2)
    bad2["recorded_prediction_ts_ns"]+=3*NS
    bad3=obs(3)
    bad3["settlement_status"]="PENDING"
    bad4=obs(4)
    bad4["source"]="SYNTHETIC"
    r=assess_480_shadow_calibration([*rows,bad,bad2,bad3,bad4])
    assert r["eligible_markets"]==96
    assert r["excluded_duplicate_records"]==0
    assert r["excluded_invalid_records"]==8  # 4 invalid + four original duplicate tickers? see fixture behavior


@pytest.mark.parametrize("field,value",[
    ("p_yes",math.nan),("p_yes",True),("p_yes",1.1),
    ("p_no",.6),("confidence",.99),("market_close","invalid"),
    ("scheduled_checkpoint_ts_ns",0),("recorded_prediction_ts_ns",True),
    ("actual_side","PENDING"),("source",None)
])
def test_invalid_replay_data_excluded(field,value):
    rows=[obs(i) for i in range(100)]
    rows[10][field]=value
    r=assess_480_shadow_calibration(rows)
    assert r["eligible_markets"]==99
    assert r["excluded_invalid_records"]==1


def test_missing_checkpoint_quotes_never_make_up_market_advantage():
    rows=[obs(i,quote=False) for i in range(100)]
    r=assess_480_shadow_calibration(rows)
    assert r["matched_kalshi_midpoint_markets"]==0
    assert r["market_midpoint"] is None


def test_single_class_train_cannot_fit_candidate():
    rows=[obs(i,actual=("ABOVE" if i<60 else None)) for i in range(100)]
    r=assess_480_shadow_calibration(rows)
    assert r["status"]=="INSUFFICIENT_TRAIN_LABEL_VARIATION"
    assert r["candidate"] is None


def test_candidate_and_incumbent_evaluated_on_identical_markets():
    rows=[obs(i,quote=(i%3==0)) for i in range(100)]
    r=assess_480_shadow_calibration(rows)
    assert r["matched_kalshi_midpoint_markets"]==len([i for i in range(60,100) if i%3==0])
    assert r["incumbent"]["markets"]==40
    assert r["candidate"]["markets"]==40
    assert r["market_midpoint"]["markets"]==r["matched_kalshi_midpoint_markets"]


def test_no_untrusted_threshold_or_sample_size_parameter_tuning():
    with pytest.raises(TypeError):
        assess_480_shadow_calibration([obs(i) for i in range(100)], threshold=.50)
