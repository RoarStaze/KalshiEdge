"""Prospective settlement-window challenger: causal, immutable, never trading."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.bootstrap.settlement_challenger_480 import (
    SettlementShadowWriter, audit_settlement_challenger, build_settlement_shadow,
    load_settlement_shadow, settlement_window_log_variance,
    settlement_window_probability,
)

NS = 1_000_000_000
START = 1_900_000_000 * NS
MODEL = "a" * 64


def test_probability_models_last_minute_average_not_terminal_tick():
    sigma = .00015
    terminal_var = sigma**2 * 480
    average_var = settlement_window_log_variance(sigma, 480.0)
    assert average_var == pytest.approx(sigma**2 * 440)
    assert average_var < terminal_var
    assert settlement_window_log_variance(sigma, 60.0) == pytest.approx(sigma**2 * 20)
    assert settlement_window_probability(
        brti_value=100000, strike=100000, sigma_per_sqrt_second=sigma,
        seconds_remaining=480.0,
    ) == pytest.approx(.5)
    up = settlement_window_probability(
        brti_value=100200, strike=100000, sigma_per_sqrt_second=sigma,
        seconds_remaining=480.0,
    )
    down = settlement_window_probability(
        brti_value=99800, strike=100000, sigma_per_sqrt_second=sigma,
        seconds_remaining=480.0,
    )
    assert .5 < up < 1
    assert 0 < down < .5
    assert up > .5 and down < .5


@pytest.mark.parametrize("value", [None, True, 0, -1, float("nan"), float("inf")])
def test_invalid_benchmark_inputs_fail_closed(value):
    with pytest.raises(ValueError):
        settlement_window_probability(
            brti_value=value, strike=100000,
            sigma_per_sqrt_second=.00015, seconds_remaining=480,
        )
    with pytest.raises(ValueError):
        settlement_window_probability(
            brti_value=100000, strike=100000,
            sigma_per_sqrt_second=value, seconds_remaining=480,
        )


def test_invalid_settlement_horizon_rejected():
    for remaining in (0, 59, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            settlement_window_probability(
                brti_value=100010, strike=100000,
                sigma_per_sqrt_second=.00015, seconds_remaining=remaining,
            )


def _make_pair(root: Path, index: int, *, forecast="ABOVE", final_p=.92,
               actual: str | None = None):
    ts = START + index * 900 * NS
    final = FinalForecastRecord(
        market_ticker=f"KXBTC15M-TEST-{index:05d}-00",
        strike=100000.0, scheduled_ts_ns=ts,
        prediction_ts_ns=ts, checkpoint_seconds=480,
        seconds_remaining=480.0, forecast=forecast,
        reason="CONFIDENCE_BELOW_69_PERCENT" if forecast=="SKIP" else None,
        confidence=2 * abs(final_p - .5),
        p_yes=final_p, p_no=1-final_p,
        model_hash=MODEL,
    )
    original_file=FinalForecastWriter(root).save(final,market_close_ts_ns=ts+480*NS)
    assert original_file is not None
    prediction=LivePredictionRecord(
        market_ticker=final.market_ticker,prediction_ts_ns=ts,
        status="OK",p_yes=final_p,p_no=1-final_p,
        predicted_side="ABOVE" if final_p >= .5 else "BELOW",
        feed_quality=FeedQuality(healthy=True),
        model_hash=MODEL,brti_value=100125 if index % 2 else 99875,
        brti_source_ts_ns=ts-NS,
        brti_shadow_risk_sigma=.00013,
        brti_shadow_risk_observed_seconds=90.0,
        brti_shadow_risk_observation_count=92,
    )
    if actual is not None:
        _save_settlement(root,final,OfficialSettlement(
            market_ticker=final.market_ticker,
            market_close_ts_ns=ts+480*NS,
            result="yes" if actual=="ABOVE" else "no",
            actual_side=actual,fetched_ts_ns=ts+900*NS,
            market_status="settled",
        ))
    return final,original_file,prediction


def test_shadow_is_bound_to_original_final_and_contains_no_outcomes(tmp_path):
    final, path, pred=_make_pair(tmp_path,1,actual="ABOVE")
    before=path.read_bytes()
    item=build_settlement_shadow(final,pred,path)
    assert item.status=="OK"
    assert item.p_yes is not None and 0 <= item.p_yes <= 1
    assert item.incumbent_p_yes==final.p_yes
    assert item.final_sha256==hashlib.sha256(before).hexdigest()
    assert item.source_ts_ns < final.prediction_ts_ns
    assert item.settlement_window_seconds==60
    writer=SettlementShadowWriter(tmp_path)
    stored=writer.save(item)
    assert stored is not None
    assert writer.save(item) is None
    assert load_settlement_shadow(tmp_path,final)==item
    assert path.read_bytes()==before
    text=stored.read_text()
    for forbidden in ('"actual_side"','"label_yes"','"settled_result"'):
        assert forbidden not in text


def test_missing_or_stale_reference_is_explicitly_unavailable(tmp_path):
    final, path, pred=_make_pair(tmp_path,2)
    late=pred.model_copy(update={"brti_source_ts_ns":final.prediction_ts_ns+NS})
    stale=pred.model_copy(update={"brti_source_ts_ns":final.prediction_ts_ns-6*NS})
    missing=pred.model_copy(update={"brti_shadow_risk_sigma":None})
    mismatch=pred.model_copy(update={"market_ticker":"KXBTC15M-OTHER"})
    for altered in (late,stale,missing,mismatch):
        item=build_settlement_shadow(final,altered,path)
        assert item.status=="UNAVAILABLE"
        assert item.p_yes is None
        assert item.reason
        # No invalid value is ever silently replaced with Binance spot.


def test_causal_shadow_detects_tampered_snapshot_and_changed_final(tmp_path):
    final,path,pred=_make_pair(tmp_path,3)
    item=build_settlement_shadow(final,pred,path)
    saved=SettlementShadowWriter(tmp_path).save(item)
    assert saved is not None
    invalid=json.loads(saved.read_text())
    invalid["p_yes"] = .001
    saved.write_text(json.dumps(invalid))
    with pytest.raises(ValueError,match="integrity|hash"):
        load_settlement_shadow(tmp_path,final)
    saved.write_text(json.dumps(item.model_dump(mode="json")))
    path.write_text(path.read_text()+" ")
    with pytest.raises(ValueError,match="final|digest|hash"):
        load_settlement_shadow(tmp_path,final)


def test_audit_uses_verified_same_market_settlements_with_missing_accounted(tmp_path):
    # Two paired, one missing shadow, one unsettled. No imaginary profitability.
    for index,actual in ((10,"ABOVE"),(11,"BELOW"),(12,"ABOVE"),(13,None)):
        final,path,pred=_make_pair(tmp_path,index,actual=actual)
        if index!=12:
            SettlementShadowWriter(tmp_path).save(build_settlement_shadow(final,pred,path))
    report=audit_settlement_challenger(tmp_path)
    assert report["status"]=="RESEARCH_ONLY"
    assert report["counts"]["paired_settled"]==2
    assert report["counts"]["missing_shadow_settled"]==1
    assert report["counts"]["pending_settlement"]==1
    assert report["incumbent_metrics"]["brier"] is not None
    assert report["challenger_metrics"]["brier"] is not None
    assert report["counts"]["settled_480"]==3
    assert report["live_model_changed"] is False
    assert report["autotrade_changed"] is False
    assert report["verified_profitability"] is False


def test_live_capture_is_after_final_saved_and_does_not_touch_execution():
    import inspect
    from kalshi_edge.bootstrap.live import _run_live_async
    source=inspect.getsource(_run_live_async)
    assert source.index("persist_prediction_tick(") < source.index("capture_settlement_shadow(")
    assert "if saved_path is not None" in source
    assert "SettlementShadowWriter" in source
    assert "capture_settlement_shadow" in source
