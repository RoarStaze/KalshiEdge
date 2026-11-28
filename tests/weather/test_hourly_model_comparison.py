"""V13 paired, chronological, true-incumbent Weather Index model comparison.

All crafted numeric cases here are SYNTHETIC; they test a replay protocol,
not superiority or profitability on the user's real saved weather database.
"""
from __future__ import annotations

import copy
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_edge.weather.hourly_hybrid import fit_hybrid, predict_hybrid
from kalshi_edge.weather.hourly_model_comparison import (
    compare_models, compare_local_weather_models,
)

UTC=timezone.utc
START=datetime(2026,8,1,0,tzinfo=UTC)


def cases(n=192, *, analogue_signal=True, market=True, frozen_live=True):
    """Deterministic sequence; generate strike BEFORE labeling its outcome."""
    result=[]
    for i in range(n):
        hour=START+timedelta(hours=i)
        phase=i*.37
        fast=2.*math.sin(phase)+.35*math.cos(phase*.17)
        slow=.8*math.sin(phase-.35)+.3*math.cos(phase*.07)
        delta=1.9*math.sin(i*.93)+.6*math.cos(i*.19)
        lead=.5
        current=56.+.8*math.cos(i*.14)
        true_delta=(.30*fast+.25*slow)*lead
        if analogue_signal:
            true_delta+=.90*delta
        noise=.07*math.sin(i*2.71)+.03*math.cos(i*1.73)
        actual=current+true_delta+noise
        # Exact strike frozen before the observed hourly outcome.
        threshold=round(current+(i%5-2)*.45,2)
        row={
            "target_utc":hour.isoformat(),"forecast_asof":(hour-timedelta(minutes=30)).isoformat(),
            "current_index_f":current,"fast_slope":fast,"slow_slope":slow,
            "hours_ahead":lead,"baseline_mean_f":current+.30*fast*lead,
            "baseline_sigma_f":1.5,"diurnal_delta_f":delta,
            "observed_index_f":actual,
        }
        if frozen_live:
            # This is a SYNTHETIC original forecast; the local benchmark
            # requires real immutable V9 forward snapshots instead.
            row.update({"live_frozen_mean_f":current+.35*fast*lead+.40*slow*lead,
                        "live_frozen_sigma_f":1.5,
                        "live_frozen_model_kind":"SYNTHETIC_FROZEN_CURRENT"})
        if market:
            row.update({"market_threshold_f":threshold,
                        "market_outcome_yes":actual>=threshold,
                        "market_evidence_kind":"KALSHI_FINALIZED_TEST_FIXTURE"})
        result.append(row)
    return result


def test_paired_walkforward_shows_model_improvement_on_known_analogue_signal():
    result=compare_models(cases())
    assert result["status"]=="PAIRED_EVIDENCE_IMPROVED",result
    assert result["eligible_for_live_promotion"] is True
    assert result["matched_hours"]==72
    assert result["verified_market_hours"]==72
    assert result["frozen_live_hours"]==72
    assert len(result["folds"])==3
    assert all(f["hours"]==24 for f in result["folds"])
    assert result["candidate"]["mae_f"]<result["incumbent"]["mae_f"]
    assert result["candidate"]["crps"]<result["incumbent"]["crps"]
    assert result["candidate"]["brier"]<result["incumbent"]["brier"]
    assert result["candidate"]["direction_accuracy"]>=result["incumbent"]["direction_accuracy"]-.01
    assert result["candidate"]["coverage_90"]>=.75
    assert result["parameters"] is not None


def test_without_verified_market_labels_cannot_claim_trading_accuracy_or_promote():
    result=compare_models(cases(market=False))
    assert result["matched_hours"]==72
    assert result["verified_market_hours"]==0
    assert result["incumbent"]["brier"] is None
    assert result["candidate"]["brier"] is None
    assert result["incumbent"]["direction_accuracy"] is None
    assert result["eligible_for_live_promotion"] is False
    assert result["status"]=="INSUFFICIENT_VERIFIED_MARKET_PAIRS"


def test_no_real_predictive_edge_does_not_pass_model_upgrade_gate():
    rows=cases(analogue_signal=False)
    for i,row in enumerate(rows):
        # A nearly persistent, low-noise process is already forecastable
        # without adding a four-parameter hybrid.
        actual=row["current_index_f"]+.02*math.sin(i*1.47)
        row["observed_index_f"]=actual
        row["market_outcome_yes"]=actual>=row["market_threshold_f"]
        row["live_frozen_mean_f"]=row["current_index_f"]
        row["live_frozen_sigma_f"]=.65
    result=compare_models(rows)
    assert result["eligible_for_live_promotion"] is False


def test_heldout_outcomes_do_not_change_final_model_parameters():
    rows=cases()
    first=compare_models(rows)
    mutated=copy.deepcopy(rows)
    # Only the last, genuinely held-out block is corrupted.
    for row in mutated[-24:]:
        row["observed_index_f"]+=12.
        row["market_outcome_yes"]=not row["market_outcome_yes"]
    second=compare_models(mutated)
    assert second["parameters"]==first["parameters"]
    assert second["candidate"]["mae_f"]>first["candidate"]["mae_f"]
    assert second["eligible_for_live_promotion"] is False


def test_degraded_market_brier_prohibits_promotion_even_with_good_temperature_mae():
    rows=cases()
    for row in rows[-72:]:
        row["market_outcome_yes"]=not row["market_outcome_yes"]
    # Even if a temperature-only contest looks attractive, contradictory
    # verified market labels must NOT produce a promotional model.
    result=compare_models(rows)
    assert result["eligible_for_live_promotion"] is False


def test_hybrid_rejects_invalid_inputs_and_does_not_leak_future_label():
    trained=fit_hybrid(cases(120))
    assert trained["sigma_scale"]>0
    feature=cases(1)[0]
    p=predict_hybrid(feature,trained)
    assert len(p)==2 and all(math.isfinite(x) for x in p)
    with pytest.raises(ValueError):
        predict_hybrid({**feature,"diurnal_delta_f":float("nan")},trained)
    with pytest.raises(ValueError):
        fit_hybrid([{**r,"diurnal_delta_f":float("inf")} for r in cases(120)])
    with pytest.raises(ValueError):
        predict_hybrid(feature,{**trained,"fast_gain":float("inf")})


def test_small_data_has_no_synthetic_success_metrics():
    report=compare_models(cases(96))
    assert report["eligible_for_live_promotion"] is False
    assert report["status"]=="INSUFFICIENT_WALKFORWARD_HOURS"
    assert report["matched_hours"]==0
    assert report["candidate"]["brier"] is None


def test_no_local_database_reports_evidence_gap_not_wins(tmp_path):
    report=compare_local_weather_models(tmp_path/"weatheredge.sqlite3")
    assert report["status"]=="NO_LOCAL_WEATHER_HISTORY"
    assert report["real_orders_enabled"] is False
    assert report["groups"]==[]
    assert report["calibration_certified"] is False
    assert report["any_promoted"] is False


def test_local_comparison_only_reads_original_timestamped_evidence(tmp_path):
    """One city's synthetic Index receipts; no validated markets means no promotion."""
    from kalshi_edge.weather.hourly import _init
    db=tmp_path/"weatheredge.sqlite3"
    with sqlite3.connect(db) as c:
        _init(c)
        for i in range(192*12):
            at=START+timedelta(minutes=i*5)
            target=round(55.+2*math.sin(i*.022),2)
            packet={"t":int(at.timestamp()*1000),"v":target,
                    "status":"normal","contributors":5}
            c.execute("""INSERT INTO weather_hourly_index
             (city,event_ms,received_at,temp_f,status,config_version,source_sha,payload)
             VALUES(?,?,?,?,?,?,?,?)""",(
                "chicago",packet["t"],(at+timedelta(minutes=1)).isoformat(),
                target,"normal","chicago-temperature-v1.1","fixture-sha",
                json.dumps(packet,sort_keys=True)))
    asof=START+timedelta(days=8,minutes=15)
    report=compare_local_weather_models(db,asof=asof,cities=("chicago",),leads=(30,))
    assert report["status"] in ("RESEARCH_SCORED","INSUFFICIENT_HISTORY")
    assert report["real_orders_enabled"] is False
    assert len(report["groups"])==1
    group=report["groups"][0]
    assert group["city"]=="chicago" and group["lead_minutes"]==30
    assert group["verified_market_hours"]==0
    assert not group["eligible_for_live_promotion"]
    assert group["incumbent"]["brier"] is None
    # Read-only audit: no candidate predictions, outcomes or labels written.
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM weather_hourly_snapshots").fetchone()[0]==0
        assert c.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='weather_hourly_forward_snapshots'").fetchone()[0]==0


def test_cache_report_is_read_only_and_fail_closed_if_corrupted(tmp_path):
    from kalshi_edge.weather.hourly_model_comparison import (
        write_local_comparison,read_local_comparison)
    db=tmp_path/"data"/"weather"/"weatheredge.sqlite3"
    db.parent.mkdir(parents=True)
    report=compare_models(cases())
    stored={"mode":"HOURLY_MODEL_PAIRED_COMPARISON_V13",
            "status":"RESEARCH_SCORED","groups":[
                {"city":"chicago","lead_minutes":30,**report}],
            "real_orders_enabled":False,"calibration_certified":False,
            "any_promoted":report["eligible_for_live_promotion"]}
    write_local_comparison(db,stored)
    loaded=read_local_comparison(db)
    assert loaded["status"]=="RESEARCH_SCORED"
    assert len(loaded["groups"])==1
    assert loaded["real_orders_enabled"] is False
    p=db.parent/"hourly-model-comparison.json"
    import json
    data=json.loads(p.read_text())
    data["groups"][0]["eligible_for_live_promotion"]=False
    p.write_text(json.dumps(data))
    assert read_local_comparison(db)["status"]=="INVALID_COMPARISON_CACHE"


def test_weather_train_renders_real_comparison_and_explicit_no_proof_state():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("train","")
    assert "Incumbent vs challenger" in html
    assert 'id="wx-model-comparison"' in html
    assert "/api/weather/hourly/model-comparison" in html
    assert "No verified market comparison" in html


def test_paired_comparison_api_returns_no_fabricated_preview_scores(tmp_path):
    from http.server import ThreadingHTTPServer
    import threading
    import httpx
    from kalshi_edge.bootstrap.preview_site import make_preview_handler
    server=ThreadingHTTPServer(("127.0.0.1",0),make_preview_handler())
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        with httpx.Client(timeout=5,trust_env=False) as c:
            r=c.get(f"http://127.0.0.1:{server.server_port}/api/weather/hourly/model-comparison")
            assert r.status_code==200
            data=r.json()
            assert data["real_orders_enabled"] is False
            assert data["any_promoted"] is False
            assert data["groups"]==[]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_prospective_live_hybrid_requires_verified_comparison_and_fresh_cache(
        tmp_path,monkeypatch):
    from kalshi_edge.weather import hourly_nowcast
    from kalshi_edge.weather.hourly import _init
    db=tmp_path/"weatheredge.sqlite3"
    conn=sqlite3.connect(db)
    _init(conn)
    rows=cases()
    params=fit_hybrid(rows[:-24])
    score=compare_models(rows)
    assert score["eligible_for_live_promotion"] is True
    payload={"mode":"HOURLY_MODEL_PAIRED_COMPARISON_V13",
             "status":"RESEARCH_SCORED","real_orders_enabled":False,
             "calibration_certified":False,"any_promoted":True,
             "asof_utc":datetime(2026,8,10,tzinfo=UTC).isoformat(),
             "groups":[{"city":"chicago","lead_minutes":30,
                         "config_version":"test-source-v1",**score}]}
    from kalshi_edge.weather.hourly_model_comparison import write_local_comparison
    write_local_comparison(db,payload)
    # Cache validity is not enough; the replay's source configuration and
    # no-future timestamp must match the live Index source.
    monkeypatch.setattr(hourly_nowcast,"_load_points",
        lambda *args,**kwargs:([
            (int((START+timedelta(hours=i)).timestamp()*1000),
             int((START+timedelta(hours=i,minutes=1)).timestamp()*1000),
             55.,"normal") for i in range(12)],None))
    # A cached score from a future date cannot authorize an earlier forecast.
    now=START+timedelta(hours=1)
    model=hourly_nowcast.index_nowcast_decision(
        conn,"chicago",now,lead_minutes=30,config_version="test-source-v1")
    assert model["model"]!=hourly_nowcast.HYBRID_MODEL
    conn.close()


def test_nowcast_hybrid_uses_only_live_features_not_future_observation():
    from kalshi_edge.weather.hourly_nowcast import (
        HYBRID_MODEL,nowcast_prediction)
    params=fit_hybrid(cases(120))
    d={"enabled":True,"model":HYBRID_MODEL,"parameters":params}
    kwargs={"diurnal_delta_f":1.8}
    predicted=nowcast_prediction(55.,1.5,2.,1.,.5,d,**kwargs)
    expected=predict_hybrid({"current_index_f":55.,"baseline_sigma_f":1.5,
              "fast_slope":2.,"slow_slope":1.,"hours_ahead":.5,
              "diurnal_delta_f":1.8},params)
    assert predicted==expected
    # No previous-day analogue at decision time => fail closed to incumbent,
    # not hallucinate future target data.
    assert nowcast_prediction(55.,1.5,2.,1.,.5,d)==(55.,1.5)


def test_hypothetical_v12_replay_cannot_replace_frozen_actual_live_forecast():
    # A new model beating a reconstructed observation component does not
    # prove superiority to the real live pipeline, which may include V1
    # settlement correction and the HRRR/NBM physical ensemble.
    result=compare_models(cases(frozen_live=False))
    assert result["matched_hours"]==72
    assert result["verified_market_hours"]==72
    assert result["frozen_live_hours"]==0
    assert result["eligible_for_live_promotion"] is False
    assert result["status"]=="INSUFFICIENT_FROZEN_LIVE_FORECASTS"


def test_early_hours_without_prior_day_analogue_cannot_poison_later_train():
    # Real 8-day replay has no previous-day analogue for its earliest hours.
    # They must not be fabricated as zero changes or block all later
    # training when enough genuinely available prior-day observations exist.
    rows=cases()
    for row in rows[:45]:
        row["diurnal_delta_f"]=None
    result=compare_models(rows)
    assert result["matched_hours"]==72
    assert result["verified_market_hours"]==72
    assert result["frozen_live_hours"]==72
    assert result["parameters"] is not None


def test_each_fold_embargoes_labels_not_published_before_first_60m_forecast():
    # For a 60-minute-ahead forecast at time T-60, the T-60 Index label
    # may not arrive until T-50. It must never be used to train that fold.
    rows=cases()
    for row in rows:
        target=datetime.fromisoformat(row["target_utc"])
        row["forecast_asof"]=(target-timedelta(minutes=60)).isoformat()
        row["label_received_at"]=(target+timedelta(minutes=10)).isoformat()
    result=compare_models(rows)
    assert result["matched_hours"]==72
    assert len(result["folds"])==3
    assert result["folds"][0]["train_hours"]==len(rows)-72-1
    assert result["folds"][1]["train_hours"]==len(rows)-48-1
    assert result["folds"][2]["train_hours"]==len(rows)-24-1


def test_future_publication_cannot_promote_on_lookahead_fit():
    rows=cases()
    for row in rows[:120]:
        row["label_received_at"]=(START+timedelta(days=100)).isoformat()
    report=compare_models(rows)
    assert report["eligible_for_live_promotion"] is False
    assert report["status"]=="INSUFFICIENT_CAUSAL_TRAINING"


def test_live_diurnal_feature_uses_actual_contract_hour_not_rounded_lead(monkeypatch):
    from kalshi_edge.weather import hourly_nowcast as hn
    observed=[]
    asof=datetime(2026,8,1,17,38,tzinfo=UTC)
    actual_target=datetime(2026,8,1,18,0,tzinfo=UTC)
    points=[(int((asof-timedelta(minutes=4)).timestamp()*1000),
             int((asof-timedelta(minutes=3)).timestamp()*1000),
             56.,"normal")]
    monkeypatch.setattr(hn,"_load_points",lambda *args,**kwargs:(points,None))
    monkeypatch.setattr(hn,"_slopes_from_points",
                        lambda *args,**kwargs:(1.,1.,56.,points[0][0]))
    monkeypatch.setattr(hn,"choose_index_nowcast",lambda *args,**kwargs:{
        "enabled":True,"model":hn.DIURNAL_MODEL,"history_hours":144,
        "status":"HISTORICAL_INDEX_HOLDOUT_IMPROVED_UNCERTIFIED"})
    monkeypatch.setattr(hn,"_diurnal_delta",
                        lambda pts,ts,now_ms,target_ms:
                            observed.append(target_ms) or 1.2)
    with sqlite3.connect(":memory:") as conn:
        d=hn.index_nowcast_decision(conn,"chicago",asof,lead_minutes=30,
                                   config_version="v1",target_utc=actual_target)
    assert d["enabled"]
    assert d["live_diurnal_delta_f"]==1.2
    assert observed==[int(actual_target.timestamp()*1000)]


def test_insufficient_local_hours_show_exact_readiness_without_fabricated_scores():
    # Reproduces the user's 2026-11-27 real local report: only 59-63
    # eligible index hours per city/lead. This is NOT a failed challenger.
    for n in (59,60,63):
        result=compare_models(cases(n))
        assert result["status"]=="INSUFFICIENT_WALKFORWARD_HOURS"
        assert result["observed_index_hours"]==n
        assert result["minimum_index_hours"]==144
        assert result["additional_eligible_hours_needed"]==144-n
        assert result["minimum_frozen_live_hours"]==72
        assert result["minimum_verified_market_pairs"]==24
        assert result["matched_hours"]==0
        assert result["eligible_for_live_promotion"] is False
        assert result["incumbent"]["mae_f"] is None
        assert result["candidate"]["mae_f"] is None


def test_weather_train_displays_eligibility_progress_without_implying_accuracy():
    from kalshi_edge.weather.hourly_ui import render_hourly_page
    html=render_hourly_page("train","")
    assert "eligible Index hours" in html
    assert "additional_eligible_hours_needed" in html
    assert "minimum_index_hours" in html
    assert "Not an estimated completion time" in html


def test_causal_or_missing_frozen_evidence_still_blocks_v13_promotion():
    result=compare_models(cases(192,market=False,frozen_live=False))
    assert result["observed_index_hours"]==192
    assert result["additional_eligible_hours_needed"]==0
    assert result["minimum_frozen_live_hours"]==72
    assert result["eligible_for_live_promotion"] is False
