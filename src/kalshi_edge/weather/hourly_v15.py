"""V15: source-time-audited NOAA HRRR/NBM fusion of a frozen V13/V14 forecast.

No reconstructed historical NOAA issuance times, fabricated station values,
market outcome features, or order placement. Only previously saved paired
Kalshi Index and NOAA forecasts can train / qualify this temperature model.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hourly_hybrid import _solve
from .hourly_nowcast import _crps
from .predictor import normal_cdf
from .hourly_physical_learning import (
    MIN_TRAIN, VALIDATION, collect_cases, lead_bucket,
)
from .status import weather_database_path

V15_KIND = "HISTORICAL_PAIRED_NOAA_FUSION_INDEX_V15"
BASELINE_KINDS = frozenset((
    "HISTORICAL_REGIME_HYBRID_INDEX_NOWCAST_V14",
    "HISTORICAL_ARCHIVE_HYBRID_INDEX_NOWCAST_V13",
    "HISTORICAL_PAIRED_HYBRID_INDEX_NOWCAST_V13",
))
Z90 = 1.64485362695
# Keep the previous forecast dominant until an actual, held-out NOAA edge
# demonstrates benefit. Only training values can select these coefficients.
SIGMA_SCALES = (.65, .80, 1., 1.20, 1.40)
SPREAD_GAINS = (0., .10, .20)


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("INVALID_V15_TIME")
    return value.astimezone(timezone.utc)


def _numeric(row: dict, field: str, low: float, high: float) -> float:
    value = row[field]
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("INVALID_V15_FEATURE_" + field.upper())
    return float(value)


def _features(row: dict) -> tuple[float, float, float, float, float]:
    baseline = _numeric(row, "baseline_f", -129., 159.)
    hrrr = _numeric(row, "hrrr_f", -129., 159.)
    nbm = _numeric(row, "nbm_f", -129., 159.)
    sigma = _numeric(row, "sigma_f", .05, 30.)
    average = (hrrr+nbm)*.5
    delta = max(-8., min(8., average-baseline))
    # Opposing NOAA deviations can contain terrain/coastal information,
    # but the signed contrast is bounded separately from the mean signal.
    contrast = max(-5., min(5., (hrrr-nbm)*.5))
    disagreement = min(1.,abs(hrrr-nbm)/4.)
    return baseline, sigma, delta, contrast, disagreement


def validate_v15(params: object) -> bool:
    if not isinstance(params, dict) or set(params) != {
        "nwp_gain","contrast_gain","bias_f","sigma_scale","spread_gain"
    }:
        return False
    try:
        return (
            0. <= _numeric(params,"nwp_gain",0.,.90) <= .90 and
            -.40 <= _numeric(params,"contrast_gain",-.40,.40) <= .40 and
            -.90 <= _numeric(params,"bias_f",-.90,.90) <= .90 and
            .65 <= _numeric(params,"sigma_scale",.65,1.40) <= 1.40 and
            0. <= _numeric(params,"spread_gain",0.,.20) <= .20
        )
    except (ValueError, TypeError, KeyError):
        return False


def predict_v15(row: dict, params: dict) -> tuple[float, float]:
    if not validate_v15(params):
        raise ValueError("INVALID_V15_PARAMETERS")
    baseline,sigma,delta,contrast,disagreement = _features(row)
    shift = params["nwp_gain"]*delta + params["contrast_gain"]*contrast + params["bias_f"]
    mean = baseline + max(-5., min(5., shift))
    sigma = max(.05,sigma*params["sigma_scale"]*
                (1. + params["spread_gain"]*disagreement))
    if not -130. < mean < 160.:
        raise ValueError("OUT_OF_RANGE_V15_PREDICTION")
    return mean,sigma


def fit_v15(rows: list[dict]) -> dict:
    """Huber/ridge 3-parameter correction; sigma selected only on train CRPS."""
    if not isinstance(rows,list) or len(rows)<MIN_TRAIN:
        raise ValueError("INSUFFICIENT_V15_TRAINING")
    cases = []
    for row in rows:
        baseline,_,delta,contrast,_ = _features(row)
        y = _numeric(row,"observed_f",-129.,159.) - baseline
        cases.append(((delta,contrast,1.),y))
    beta=[0.,0.,0.]
    prior=(0.,0.,0.)
    ridge=(20.,45.,50.)
    for _ in range(5):
        gram = [[ridge[i] if i==j else 0. for j in range(3)]
                for i in range(3)]
        rhs = [ridge[i]*prior[i] for i in range(3)]
        for x,y in cases:
            residual = y - sum(beta[j]*x[j] for j in range(3))
            weight = min(1.,1.5/max(.01,abs(residual)))
            for i in range(3):
                rhs[i] += weight*x[i]*y
                for j in range(3):
                    gram[i][j] += weight*x[i]*x[j]
        solved=_solve(gram,rhs)
        beta=[
            max(0.,min(.90,solved[0])),
            max(-.40,min(.40,solved[1])),
            max(-.90,min(.90,solved[2])),
        ]
    seed={"nwp_gain":round(beta[0],6),
          "contrast_gain":round(beta[1],6),
          "bias_f":round(beta[2],6),
          "sigma_scale":1.,"spread_gain":0.}
    def score(scale: float,spread: float) -> float:
        params={**seed,"sigma_scale":scale,"spread_gain":spread}
        total=0.
        for row in rows:
            mean,sigma=predict_v15(row,params)
            total+=_crps(mean,sigma,_numeric(row,"observed_f",-129.,159.))
        # Mild prior toward no extra spread treatment if scores tie.
        return total/len(rows)+.002*spread
    _,scale,spread=min((score(scale,spread),scale,spread)
                       for scale in SIGMA_SCALES for spread in SPREAD_GAINS)
    params={**seed,"sigma_scale":scale,"spread_gain":spread}
    if not validate_v15(params):
        raise ValueError("INVALID_FITTED_V15")
    return params


def _metrics(rows: list[dict], params: dict | None) -> dict:
    if not rows:
        raise ValueError("EMPTY_V15_METRICS")
    mae=crps=coverage=brier=log_loss=direction=market=0.
    for row in rows:
        baseline,baseline_sigma,*_=_features(row)
        mean,sigma=(baseline,baseline_sigma) if params is None else predict_v15(row,params)
        actual=_numeric(row,"observed_f",-129.,159.)
        mae+=abs(actual-mean)
        crps+=_crps(mean,sigma,actual)
        coverage+=abs(actual-mean)<=Z90*sigma
        if row.get("market_verified") is True:
            threshold=_numeric(row,"threshold_f",-129.,159.)
            outcome=actual>=threshold
            prob=max(1e-8,min(1.-1e-8,1-normal_cdf(threshold,mean,sigma)))
            brier+=(prob-int(outcome))**2
            log_loss-=math.log(prob if outcome else 1.-prob)
            direction+=int((prob>=.5)==outcome)
            market+=1
    n=len(rows)
    return {"hours":n,"mae_f":round(mae/n,6),"crps":round(crps/n,6),
            "coverage_90":round(coverage/n,6),"market_hours":int(market),
            "brier":round(brier/market,6) if market else None,
            "log_loss":round(log_loss/market,6) if market else None,
            "direction_accuracy":round(direction/market,6) if market else None}


def _eligible_suffix(rows: list[dict]) -> list[dict]:
    ordered=sorted(rows,key=lambda r:_utc(r["target_utc"]))
    if not ordered:
        return []
    kind=ordered[-1].get("baseline_kind")
    config=ordered[-1].get("config_version")
    if kind not in BASELINE_KINDS or not isinstance(config,str) or not config:
        return []
    suffix=[]
    last=None
    for row in reversed(ordered):
        stamp=_utc(row["target_utc"])
        if (row.get("baseline_kind") != kind or
                row.get("config_version") != config or
                (last is not None and last-stamp != timedelta(hours=1))):
            break
        suffix.append(row)
        last=stamp
    return list(reversed(suffix))


def select_v15(rows: list[dict]) -> dict:
    """64 train + 24 untouched hours, compared with same frozen V13/V14."""
    report={"model_kind":V15_KIND,"promotable":False,
            "status":"INSUFFICIENT_PAIRED_HOURS",
            "history_hours":0,"train_hours":0,"validation_hours":0,
            "baseline_kind":None,"parameters":None,
            "incumbent":None,"candidate":None,
            "real_orders_enabled":False,"calibration_certified":False}
    try:
        suffix=_eligible_suffix(rows)
        report["history_hours"]=len(suffix)
        if len(suffix)<MIN_TRAIN+VALIDATION:
            return report
        train=suffix[:-VALIDATION]
        valid=suffix[-VALIDATION:]
        first_asof=_utc(valid[0].get("asof",
            _utc(valid[0]["target_utc"])-timedelta(minutes=60)))
        older=[
            r for r in train
            if _utc(r.get("label_received_at",
                _utc(r["target_utc"])+timedelta(minutes=10)))<=first_asof
        ]
        report["train_hours"]=len(older)
        report["validation_hours"]=len(valid)
        report["baseline_kind"]=valid[-1]["baseline_kind"]
        if len(older)<MIN_TRAIN:
            report["status"]="INSUFFICIENT_CAUSAL_TRAINING"
            return report
        params=fit_v15(older)
        old,new=_metrics(valid,None),_metrics(valid,params)
        report.update({"parameters":params,"incumbent":old,"candidate":new,
                       "market_verified_hours":new["market_hours"]})
        if (new["mae_f"]<=old["mae_f"]-.05 and
                new["crps"]<=old["crps"]-.025 and
                new["coverage_90"]>=.75 and
                new["coverage_90"]>=old["coverage_90"]-.10):
            report["promotable"]=True
            report["status"]="V15_HOLDOUT_IMPROVED"
        else:
            report["status"]="V15_HOLDOUT_NOT_IMPROVED"
    except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError):
        report["status"]="INVALID_V15_PAIRED_EVIDENCE"
    return report


def evaluate_v15_pairs(rows: list[dict]) -> dict:
    """Expanding-origin block experiment. Only original frozen cases qualify."""
    ordered=sorted(rows,key=lambda r:_utc(r["target_utc"]))
    block_metrics=[]
    for end in range(MIN_TRAIN+VALIDATION,len(ordered)+1,VALIDATION):
        segment=ordered[:end]
        score=select_v15(segment)
        if score["incumbent"] is not None and score["candidate"] is not None:
            block_metrics.append({
                "start_target_utc":segment[-VALIDATION]["target_utc"],
                "end_target_utc":segment[-1]["target_utc"],
                "baseline_kind":score["baseline_kind"],
                "incumbent":score["incumbent"],
                "candidate":score["candidate"]})
    return {
        "status":"V15_RESEARCH_SCORED" if block_metrics else "INSUFFICIENT_PAIRED_HOURS",
        "model_kind":V15_KIND,"evaluated_hours":sum(x["candidate"]["hours"] for x in block_metrics),
        "folds":block_metrics,
        "market_verified_hours":sum(x["candidate"]["market_hours"] for x in block_metrics),
        "brier":None,  # no pooled market score if thresholds differ / are absent
        "real_orders_enabled":False,"calibration_certified":False,
        "evidence_tier":"LOCALLY_FROZEN_NOAA_AND_INDEX_UNCERTIFIED",
    }


def v15_live_decision(conn: sqlite3.Connection,city: str,
                      config_version: str,target: datetime,asof: datetime,
                      lead_minutes: int,physics: dict, *,
                      baseline_kind: str | None) -> dict:
    """Read-only derived decision; invalid/late NOAA remains incumbent."""
    output={"enabled":False,"status":"NO_V15_ELIGIBLE_PAIRED_EVIDENCE",
            "model_kind":V15_KIND,"market_verified_hours":0}
    try:
        now,target=_utc(asof),_utc(target)
        if (baseline_kind not in BASELINE_KINDS or
                lead_minutes not in (5,15,30,45,60) or
                not now<target or
                physics.get("status")!="RESEARCH_ONLY_NOT_PROMOTED" or
                physics.get("calibration_version")!=config_version or
                _utc(physics["forecast_target_utc"])!=target):
            return output
        for model in ("HRRR","NBM"):
            if not (_utc(physics["model_runs"][model]) <=
                    _utc(physics["model_receipts"][model]) <= now):
                return output
        # Future instant / wrong-config source never authorizes forecasting.
        _numeric({"hrrr_f":physics["hrrr_index_f"]},"hrrr_f",-129.,159.)
        _numeric({"nbm_f":physics["nbm_index_f"]},"nbm_f",-129.,159.)
        cases=collect_cases(conn,city,config_version,lead_minutes,now)
        if (not cases or
            now-_utc(cases[-1]["target_utc"])>timedelta(hours=48)):
            output["status"]="V15_LOCAL_SOURCE_HISTORY_STALE_OR_MISSING"
            return output
        decision=select_v15(cases)
        output["status"]=decision["status"]
        output["history_hours"]=decision["history_hours"]
        output["train_hours"]=decision["train_hours"]
        output["validation_hours"]=decision["validation_hours"]
        output["market_verified_hours"]=decision.get("market_verified_hours",0)
        output["evidence"]=decision
        if decision["promotable"] and decision["baseline_kind"]==baseline_kind:
            output.update({"enabled":True,"status":"V15_PAIRED_TEMPERATURE_IMPROVED",
                           "parameters":decision["parameters"],"physics":physics})
    except (ValueError,TypeError,KeyError,OverflowError,sqlite3.Error):
        output["status"]="INVALID_V15_LIVE_EVIDENCE"
    return output


def compare_local_v15(db_path: Path, *, asof: datetime | None = None) -> dict:
    """Research-only, read-only study of genuinely recorded physical pairs."""
    from .hourly import CITIES
    now=_utc(asof or datetime.now(timezone.utc))
    report={"mode":"WEATHEREDGE_V15_PAIRED_NOAA_RESEARCH",
            "status":"NO_LOCAL_WEATHER_HISTORY",
            "asof_utc":now.isoformat(),"model_kind":V15_KIND,
            "real_orders_enabled":False,"calibration_certified":False,
            "groups":[]}
    path=Path(db_path)
    if not path.is_file():
        return report
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                    uri=True,timeout=10)) as conn:
            conn.execute("PRAGMA query_only=ON")
            tables={row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "weather_hourly_physical_pairs" not in tables:
                report["status"]="COLLECTING_POINT_IN_TIME_NOAA_PAIRS"
                return report
            pairs=conn.execute(
                "SELECT DISTINCT city,config_version,lead_minutes FROM weather_hourly_physical_pairs "
                "ORDER BY city,config_version,lead_minutes").fetchall()
            for city,cfg,lead in pairs:
                if city not in CITIES:
                    continue
                cases=collect_cases(conn,city,cfg,lead,now)
                score=evaluate_v15_pairs(cases)
                latest=select_v15(cases)
                report["groups"].append({
                    "city":city,"config_version":cfg,"lead_minutes":lead,
                    "paired_hours":len(cases),"latest":latest,"walkforward":score})
            report["status"]=(
                "V15_RESEARCH_SCORED" if any(
                    g["walkforward"]["evaluated_hours"] for g in report["groups"])
                else "COLLECTING_POINT_IN_TIME_NOAA_PAIRS")
    except (sqlite3.Error,ValueError,TypeError,KeyError,OverflowError):
        report["status"]="V15_SOURCE_EVIDENCE_UNAVAILABLE"
        report["groups"]=[]
    return report


def main() -> int:
    from kalshi_edge.bootstrap.config import BootstrapSettings
    parser=argparse.ArgumentParser(description=(
        "Score V15 HRRR/NBM fusion only on previously frozen point-in-time pairs."))
    parser.add_argument("--db",type=Path,default=None,
                        help="Exact existing local WeatherEdge SQLite database.")
    parser.add_argument("--json",action="store_true",
                        help="Show full evidence report instead of compact comparison.")
    args=parser.parse_args()
    path=args.db or weather_database_path(BootstrapSettings().bootstrap_dir)
    report=compare_local_v15(path)
    if args.json:
        print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
    else:
        print("WeatherEdge V15 | "+report["status"])
        print("City          Lead  Pairs  Baseline vs V15         Local promotion")
        for group in report["groups"]:
            candidate=group["latest"]
            old=candidate.get("incumbent") or {}
            new=candidate.get("candidate") or {}
            comparison=(f'{old["mae_f"]:.4f}F / {new["mae_f"]:.4f}F'
                        if old and new else "not enough paired hours")
            print(f'{group["city"]:<13} {group["lead_minutes"]:>3}m'
                  f' {group["paired_hours"]:>6}  {comparison:<27} '
                  f'{candidate["status"]}')
        print("Research-only NOAA + Kalshi Index pairs; weather orders disabled.")
    return 0 if report["status"]!="V15_SOURCE_EVIDENCE_UNAVAILABLE" else 2


if __name__=="__main__":
    raise SystemExit(main())
