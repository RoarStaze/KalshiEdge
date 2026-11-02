"""Test-first research-only same-checkpoint prospective shadow snapshots."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord
from kalshi_edge.bootstrap.frozen_specialist_480 import fit_frozen_specialist
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.shadow_live_480 import (
    Shadow480Writer, build_shadow_record, load_shadow_record,
)
from kalshi_edge.bootstrap.types import FeatureRow, FeedQuality

NS = 1_000_000_000
BASE = 1_900_000_000 * NS
TICKER = "KXBTC15M-28JAN250800-00"


@pytest.fixture(scope="module")
def frozen():
    from sklearn.linear_model import LogisticRegression  # installed in test environment
    rows = []
    for i in range(100):
        t = BASE + i * 900 * NS
        yes = i % 2 == 0
        m = .70 if yes else .30
        rows.append(FeatureRow(
            market_ticker=f"KXBTC15M-TEST-{i}", checkpoint_ts_ns=t,
            label_yes=int(yes),
            features={
                "seconds_remaining": 480.0,
                "btc_normalized_distance": 2.0 if yes else -2.0,
                "btc_realized_vol_60s": .0003,
                "btc_realized_vol_300s": .0004,
                "btc_return_60s": .001 if yes else -.001,
                "btc_return_60s_available": 1.0,
                "kalshi_quote_available": 1.0,
                "kalshi_yes_bid": m-.025, "kalshi_yes_ask": m+.025,
                "kalshi_mid": m, "kalshi_spread": .05,
                "kalshi_quote_staleness_seconds": 60.0,
            },
            source_max_ts_ns={"binance": t-NS, "kalshi_candles": t-60*NS},
        ))
    return fit_frozen_specialist(rows, dataset_sha256="a"*64,
                                frozen_at_ns=BASE + 101*900*NS)


def observation(frozen, *, odds=.95):
    now = frozen["frozen_at_ns"] + 900*NS
    final = FinalForecastRecord(
        market_ticker=TICKER, strike=101000.0,
        scheduled_ts_ns=now, checkpoint_seconds=480,
        prediction_ts_ns=now, seconds_remaining=480,
        forecast="ABOVE", confidence=.90,
        directional_probability=odds, p_yes=odds, p_no=1-odds,
        model_hash="b"*64, kalshi_yes_bid=.88, kalshi_yes_ask=.92,
    )
    prediction = LivePredictionRecord(
        market_ticker=TICKER, prediction_ts_ns=now, status="OK",
        p_yes=odds, p_no=1-odds, predicted_side="ABOVE",
        confidence=.90, feed_quality=FeedQuality(healthy=True),
        model_hash="b"*64, seconds_remaining=480,
    )
    row = FeatureRow(
        market_ticker=TICKER, checkpoint_ts_ns=now, label_yes=0,
        features={
            "seconds_remaining": 480.0,
            "btc_normalized_distance": 2.0,
            "btc_realized_vol_60s": .0003,
            "btc_realized_vol_300s": .0004,
            "btc_return_60s": .001,
            "btc_return_60s_available": 1.0,
            "kalshi_quote_available": 1.0,
            "kalshi_yes_bid": .65,
            "kalshi_yes_ask": .70,
            "kalshi_mid": .675,
            "kalshi_spread": .05,
            "kalshi_quote_staleness_seconds": 60.0,
        },
        source_max_ts_ns={"binance": now-NS, "kalshi_candles": now-60*NS},
    )
    return final,prediction,row


def test_shadow_matches_immutable_final_timestamp_and_does_not_change_it(frozen):
    final, prediction, row = observation(frozen)
    before = final.model_dump()
    record = build_shadow_record(frozen, final=final, prediction=prediction, row=row)
    assert record.status == "OK"
    assert record.p_yes is not None and 0 <= record.p_yes <= 1
    assert record.incumbent_p_yes == final.p_yes
    assert record.incumbent_model_hash == final.model_hash
    assert record.frozen_model_sha256 == frozen["sha256"]
    assert record.prediction_ts_ns == final.prediction_ts_ns
    assert record.scheduled_ts_ns == final.scheduled_ts_ns
    assert record.market_close_ts_ns == final.scheduled_ts_ns + 480*NS
    assert record.gate_qualified == (2*abs(record.p_yes-.5) >= .69)
    assert final.model_dump() == before


def test_shadow_never_invents_score_on_missing_feed_or_invalid_features(frozen):
    final, prediction, row = observation(frozen)
    invalid = row.model_copy(update={"features":{**row.features, "kalshi_quote_available":0.0}})
    fail = build_shadow_record(frozen,final=final,prediction=prediction,row=invalid)
    assert fail.status == "SKIP"
    assert fail.reason is not None
    assert fail.p_yes is None
    assert fail.gate_qualified is None
    fail2 = build_shadow_record(frozen,final=final,prediction=prediction,row=None)
    assert fail2.status == "SKIP"
    assert fail2.reason == "SHADOW_FEATURE_ROW_UNAVAILABLE"

    unhealthy = prediction.model_copy(update={
        "status":"NO_PREDICTION", "reason":"STALE_BRTI",
        "feed_quality":FeedQuality(healthy=False,reasons=("STALE_BRTI",)),
        "p_yes":None, "p_no":None,"predicted_side":None,
    })
    fail3=build_shadow_record(frozen,final=final,prediction=unhealthy,row=row)
    assert fail3.status == "SKIP" and fail3.reason == "STALE_BRTI"


def test_shadow_invalid_checkpoint_mismatch_or_pre_freeze_is_skip_not_future_prediction(frozen):
    final, pred, row=observation(frozen)
    late=final.model_copy(update={"prediction_ts_ns":final.scheduled_ts_ns+3*NS})
    result=build_shadow_record(frozen,final=late,prediction=pred,row=row)
    assert result.status=="SKIP"
    before=row.model_copy(update={"checkpoint_ts_ns":frozen["frozen_at_ns"]})
    result2=build_shadow_record(frozen,final=final,prediction=pred,row=before)
    assert result2.status=="SKIP"
    mismatch=row.model_copy(update={"market_ticker":"KXBTC15M-OTHER"})
    assert build_shadow_record(frozen,final=final,prediction=pred,row=mismatch).status=="SKIP"


def test_shadow_writer_exclusive_and_tampering_fails(tmp_path: Path,frozen):
    final,pred,row=observation(frozen)
    record=build_shadow_record(frozen,final=final,prediction=pred,row=row)
    writer=Shadow480Writer(tmp_path)
    path=writer.save(record)
    assert "live_shadow" in path.parts
    assert load_shadow_record(path)==record
    assert writer.save(record) is None
    changed=json.loads(path.read_text("utf-8"))
    changed["p_yes"]=.10
    path.write_text(json.dumps(changed),encoding="utf-8")
    with pytest.raises(ValueError,match="integrity|hash"):
        load_shadow_record(path)


def test_shadow_writer_fails_closed_for_mismatched_existing_ticker(tmp_path: Path,frozen):
    final,pred,row=observation(frozen)
    record=build_shadow_record(frozen,final=final,prediction=pred,row=row)
    writer=Shadow480Writer(tmp_path)
    path=writer.save(record)
    assert path is not None
    path.write_text("{}",encoding="utf-8")
    with pytest.raises(ValueError):
        writer.save(record)


def test_live_loop_only_records_after_immutable_final_was_saved():
    import inspect
    from kalshi_edge.bootstrap.live import _run_live_async
    source = inspect.getsource(_run_live_async)
    assert source.index("persist_prediction_tick(") < source.index("capture_research_shadow(")
    assert "if saved_path is not None" in source
