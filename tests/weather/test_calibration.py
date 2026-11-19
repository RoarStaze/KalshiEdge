"""Calibration audits must separate prospective evidence from model assumptions."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kalshi_edge.weather.calibration import (
    audit_settlement_evidence, evaluate_station,
    calibration_status, score_gaussian_degree, wilson_interval,
)


def _forecast(station, day, asof, mean=65., sigma=3., nbm=65., observed=55.):
    return {
        "station":station, "climate_day":day, "asof":asof,
        "checkpoint_hour_standard":12,
        "forecast":{"status":"READY_RESEARCH",
                    "station":station,"asof":asof,"climate_day":day,
                    "mean_f":mean,"sigma_f":sigma,
                    "nbm_max_f":nbm,"observed_max_f":observed,
                    "model_kind":"BASELINE_UNCALIBRATED",
                    "tradable":False,"settlement_verified":False}
    }


def test_fixed_degree_score_normalized_and_baseline_comparable():
    exact=score_gaussian_degree(65.,3.,65.)
    wrong=score_gaussian_degree(71.,3.,65.)
    assert exact["log_loss"] < wrong["log_loss"]
    assert exact["multiclass_brier"] < wrong["multiclass_brier"]
    assert math.isfinite(wrong["log_loss"])
    assert 0 <= exact["multiclass_brier"] <= 2
    # Fixed bins, even when means differ; far-tail outcome is still represented.
    outlier=score_gaussian_degree(-3.,3.,120.)
    assert outlier["log_loss"]>exact["log_loss"]
    assert 0 <= outlier["multiclass_brier"] <= 2


def test_empirical_90_coverage_is_measured_not_assumed():
    rows=[]
    evidence=[]
    origin=datetime(2026,10,1,18,tzinfo=timezone.utc)
    for i in range(20):
        moment=origin+timedelta(days=i)
        day=moment.date().isoformat()
        rows.append(_forecast("KORD",day,moment.isoformat()))
        evidence.append({"station":"KORD","climate_day":day,
                         "actual_max_f":65. if i < 15 else 79.,
                         "evidence_state":"MANUAL_REVIEWED_UNVERIFIED"})
    report=evaluate_station("KORD",rows,evidence)
    assert report["matched_days"]==20
    assert report["90_interval"]["covered_days"]==15
    assert report["90_interval"]["empirical_coverage"]==0.75
    low,high=report["90_interval"]["wilson_95_ci"]
    assert low <= .75 <= high
    assert report["calibration_certified"] is False
    assert report["model_improvement_verified"] is False


def test_repeated_forecasts_for_same_day_do_not_cherry_pick_the_best():
    t=datetime(2026,11,19,18,5,tzinfo=timezone.utc)
    day="2026-11-19"
    earlier=_forecast("KORD",day,t.isoformat(),mean=55.)
    later=_forecast("KORD",day,(t+timedelta(minutes=35)).isoformat(),mean=75.)
    labels=[{"station":"KORD","climate_day":day,
             "actual_max_f":75.,"evidence_state":"MANUAL_REVIEWED_UNVERIFIED"}]
    score=evaluate_station("KORD",[later,earlier],labels,min_days=1)
    assert score["matched_days"]==1
    assert score["mae_f"]==20.0


def test_invalid_settlement_capture_is_rejected_and_never_scored(tmp_path):
    root=tmp_path/"weather"
    (root/"evidence").mkdir(parents=True)
    original=(root/"evidence"/"KORD-20261119.txt")
    original.write_bytes(b"first-party-capture-needs-review")
    t=datetime(2026,11,20,12,tzinfo=timezone.utc).isoformat()
    record={
       "station":"KORD","climate_day":"2026-11-19",
       "actual_max_f":65,
       "market_ticker":"KXHIGHCHI-TEST",
       "market_rules_source":"THE_WEATHER_COMPANY",
       "official_source":"THE_WEATHER_COMPANY",
       "source_url":"https://weather.com/kalshi",
       "capture_file":"KORD-20261119.txt",
       "sha256":hashlib.sha256(original.read_bytes()).hexdigest(),
       "reviewed_at":t,
    }
    manifest=root/"settlement_evidence.jsonl"
    manifest.write_text(json.dumps(record)+"\n")
    accepted,rejected=audit_settlement_evidence(root)
    assert len(accepted)==1 and not rejected
    assert accepted[0]["evidence_state"]=="MANUAL_REVIEWED_UNVERIFIED"
    for change in (
        {"sha256":"0"*64},
        {"market_rules_source":"NWS_CLI"},
        {"capture_file":"../../outside.txt"},
        {"source_url":"https://example.com"},
    ):
        manifest.write_text(json.dumps({**record,**change})+"\n")
        invalid,reason=audit_settlement_evidence(root)
        assert invalid==[]
        assert len(reason)==1


def test_no_outcomes_means_no_fake_scores_or_90_coverage(tmp_path):
    root=tmp_path/"data"/"bootstrap"
    report=calibration_status(root)
    assert report["mode"]=="PAPER_ONLY"
    assert report["calibration_certified"] is False
    assert report["status"]=="WAITING_FOR_SETTLEMENT_EVIDENCE"
    assert report["matched_days"]==0
    assert all(x["90_interval"] is None for x in report["stations"])
    assert all(x["multiclass_brier"] is None for x in report["stations"])


def test_wilson_interval_contains_observed_rate():
    a,b=wilson_interval(9,10)
    assert a < .9 < b and b <= 1



def test_weather_calibration_pages_show_missing_evidence_not_guaranteed_90_percent():
    from kalshi_edge.weather.research_ui import render_weather_page
    for page in ("forecast","train","history"):
        html=render_weather_page(page)
        assert "/api/weather/calibration" in html
        assert 'id="wx-calibration-state"' in html
        assert 'id="wx-calibration-noon-days"' in html
        assert 'id="wx-calibration-coverage"' in html
        assert "Not certified" in html or "NOT CERTIFIED" in html
        assert "calibration_certified" in html
        assert "/api/manual-trade/" not in html
    assert "90% modeled interval (not validated)" in render_weather_page("forecast")


def test_calibration_endpoint_live_and_preview_do_not_submit_orders(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    import httpx
    from kalshi_edge.bootstrap.final_history import _make_handler
    from kalshi_edge.bootstrap.preview_site import make_preview_handler
    for preview in (False,True):
        handler=(make_preview_handler() if preview else
                 _make_handler(tmp_path/"bootstrap",credential_vault=SimpleNamespace(exists=lambda:True)))
        server=ThreadingHTTPServer(("127.0.0.1",0),handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            url=f"http://127.0.0.1:{server.server_port}/api/weather/calibration"
            with httpx.Client(timeout=5,trust_env=False) as client:
                response=client.get(url)
                assert response.status_code==200
                data=response.json()
                assert data["mode"]=="PAPER_ONLY"
                assert data["calibration_certified"] is False
                assert data["matched_days"]==0
                assert all(row["90_interval"] is None for row in data["stations"])
                assert client.post(url,json={}).status_code in (403,404)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)
