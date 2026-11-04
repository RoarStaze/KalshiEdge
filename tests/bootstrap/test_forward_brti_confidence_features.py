"""Point-in-time BRTI reference features for future 480s confidence research.

No real orders, model fitting, threshold changes, or promotion occur.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
    Forward480FeatureWriter, capture_forward_480_feature, load_forward_snapshot,
)
from kalshi_edge.bootstrap.types import FeatureRow

NS = 1_000_000_000
TICKER = "KXBTC15M-BRTI-RESEARCH"
CHECKPOINT = 1_900_000_000 * NS
HASH = "a" * 64


def fixture(tmp_path):
    final = FinalForecastRecord(
        market_ticker=TICKER, strike=100_000.0,
        scheduled_ts_ns=CHECKPOINT, prediction_ts_ns=CHECKPOINT,
        seconds_remaining=480.0, checkpoint_seconds=480,
        forecast="ABOVE", confidence=0.8, p_yes=0.9, p_no=0.1,
        model_hash=HASH,
    )
    path = FinalForecastWriter(tmp_path).save(
        final, market_close_ts_ns=CHECKPOINT + 480 * NS
    )
    assert path is not None
    row = FeatureRow(
        market_ticker=TICKER, checkpoint_ts_ns=CHECKPOINT, label_yes=0,
        features={"seconds_remaining":480.0,"btc_normalized_distance":0.1},
        source_max_ts_ns={"binance":CHECKPOINT-NS,"kalshi_candles":CHECKPOINT-60*NS},
    )
    state = SimpleNamespace(feature_row=lambda ts: row)
    prediction = SimpleNamespace(
        prediction_ts_ns=CHECKPOINT, market_ticker=TICKER, model_hash=HASH,
        status="OK", feed_quality=SimpleNamespace(healthy=True),
        brti_value=100_250.0, btc_reference=100_000.0,
        brti_source_ts_ns=CHECKPOINT-NS,
        binance_source_ts_ns=CHECKPOINT-NS,
        brti_binance_gap_bps=25.0,
    )
    return final,path,state,prediction


def test_480_snapshot_captures_fresh_brti_strike_and_reference_gap_without_labels(tmp_path):
    final,path,state,prediction=fixture(tmp_path)
    writer=Forward480FeatureWriter(tmp_path)
    saved=capture_forward_480_feature(
        state=state,prediction=prediction,final=final,
        forecast_file=path,writer=writer,
    )
    assert saved is not None
    snapshot=load_forward_snapshot(tmp_path, final)
    assert snapshot.status=="FEATURES_OK"
    assert snapshot.features["brti_distance_to_strike_bps"]==pytest.approx(25.0)
    assert snapshot.features["brti_binance_gap_bps"]==pytest.approx(25.0)
    assert snapshot.source_max_ts_ns["brti"]==CHECKPOINT-NS
    assert "label_yes" not in saved.read_text()
    assert "actual_side" not in saved.read_text()
    assert writer.save(snapshot) is None
    raw=json.loads(saved.read_text())
    raw["features"]["brti_distance_to_strike_bps"]=999.0
    saved.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_forward_snapshot(tmp_path,final)


@pytest.mark.parametrize("changes",[
    {"brti_source_ts_ns":CHECKPOINT+NS},
    {"brti_source_ts_ns":CHECKPOINT-10*NS},
    {"binance_source_ts_ns":CHECKPOINT+NS},
    {"binance_source_ts_ns":CHECKPOINT-10*NS},
    {"brti_value":float("nan")},
    {"brti_value":0.0},
    {"btc_reference":0.0},
    {"brti_binance_gap_bps":-25.0},
])
def test_bad_or_future_brti_reference_never_creates_fake_training_signal(tmp_path,changes):
    final,path,state,prediction=fixture(tmp_path)
    for key,value in changes.items():
        setattr(prediction,key,value)
    saved=capture_forward_480_feature(
        state=state,prediction=prediction,final=final,
        forecast_file=path,writer=Forward480FeatureWriter(tmp_path),
    )
    assert saved is not None
    snapshot=load_forward_snapshot(tmp_path,final)
    assert snapshot.status=="FEATURES_OK"
    assert "brti_distance_to_strike_bps" not in snapshot.features
    assert "brti_binance_gap_bps" not in snapshot.features
    assert "brti" not in snapshot.source_max_ts_ns
    assert snapshot.features["btc_normalized_distance"]==0.1


def test_audit_counts_only_verified_settled_brti_feature_examples(tmp_path):
    from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        evaluate_forward_feature_evidence,
    )
    final,path,state,prediction=fixture(tmp_path)
    capture_forward_480_feature(
        state=state,prediction=prediction,final=final,
        forecast_file=path,writer=Forward480FeatureWriter(tmp_path),
    )
    close=CHECKPOINT+480*NS
    _save_settlement(tmp_path,final,OfficialSettlement(
        market_ticker=TICKER,market_close_ts_ns=close,
        result="yes",actual_side="ABOVE",market_status="settled",
        source="kalshi-public-market-api",fetched_ts_ns=close+90*NS,
    ))
    report=evaluate_forward_feature_evidence(tmp_path)
    assert report["verified_feature_examples"]==1
    assert report["verified_brti_feature_examples"]==1
    assert report["model_trained"] is False
