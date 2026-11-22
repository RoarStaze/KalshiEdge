"""V4 point-in-time Kalshi Index / NOAA physical-ensemble evaluation.

Research case construction uses first locally received official Kalshi minute
observations. Model selection uses only older paired cases; fixed coefficients
are frozen BEFORE an untouched prospective 32-hour lockbox. Production use
requires independently reconciled market evidence in that lockbox as well as
proper point and threshold scores. No orders, synthetic labels, or automatic
calibration certification are created.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hourly_nowcast import _crps
from .predictor import normal_cdf

LEADS = (5, 15, 30, 45, 60)
MIN_TRAIN = 64
VALIDATION = 24
LOCKBOX = 32
MIN_LOCKBOX_MARKETS = 24
MAX_CASES = 600
Z90 = 1.6448536269514722


def _utc(value: str | datetime) -> datetime:
    obj=datetime.fromisoformat(value.replace("Z","+00:00")) if isinstance(value,str) else value
    if obj.tzinfo is None or obj.utcoffset() is None:
        raise ValueError("NAIVE_WEATHER_EVIDENCE_TIME")
    return obj.astimezone(timezone.utc)


def _finite(value: object, lo: float = -130., hi: float = 160.) -> float:
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError("INVALID_WEATHER_FORECAST_VALUE")
    n=float(value)
    if not math.isfinite(n) or not lo<n<hi:
        raise ValueError("INVALID_WEATHER_FORECAST_VALUE")
    return n


def _canonical(obj: dict) -> str:
    return json.dumps(obj,sort_keys=True,allow_nan=False,separators=(",",":"))


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lead_bucket(minutes: float) -> int | None:
    if not isinstance(minutes,(int,float)) or isinstance(minutes,bool) or not math.isfinite(minutes):
        return None
    if not 2<=minutes<=65:
        return None
    return min(LEADS,key=lambda value:abs(value-minutes))


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_physical_pairs(
      city TEXT NOT NULL,target_utc TEXT NOT NULL,lead_minutes INTEGER NOT NULL,
      asof TEXT NOT NULL,config_version TEXT NOT NULL,
      frozen_json TEXT NOT NULL,frozen_sha TEXT NOT NULL,
      PRIMARY KEY(city,target_utc,lead_minutes))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS weather_physical_pair_config
      ON weather_hourly_physical_pairs(city,config_version,lead_minutes,target_utc)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_physical_locks(
      city TEXT NOT NULL,config_version TEXT NOT NULL,lead_minutes INTEGER NOT NULL,
      locked_at TEXT NOT NULL,params_json TEXT NOT NULL,params_sha TEXT NOT NULL,
      PRIMARY KEY(city,config_version,lead_minutes))""")


def _estimate(row: dict, weights: list[float] | tuple[float,...],
              bias: float) -> float:
    return (weights[0]*row["baseline_f"] + weights[1]*row["hrrr_f"] +
            weights[2]*row["nbm_f"] + bias)


def _metrics(rows: list[dict], weights: tuple | list | None = None,
             bias: float = 0., sigma_scale: float = 1.) -> dict:
    if not rows:
        raise ValueError("EMPTY_SCORE_SET")
    mae=crps=0.0
    covered=0
    brier=logloss=0.0
    verified=0
    for row in rows:
        mean=row["baseline_f"] if weights is None else _estimate(row,weights,bias)
        sigma=max(.05,row["sigma_f"]*sigma_scale)
        actual=row["observed_f"]
        mae+=abs(mean-actual)
        crps+=_crps(mean,sigma,actual)
        covered+=abs(mean-actual)<=Z90*sigma
        if row.get("market_verified") and row.get("threshold_f") is not None:
            y=int(actual>=row["threshold_f"])
            p=max(1e-8,min(1-1e-8,1-normal_cdf(row["threshold_f"],mean,sigma)))
            verified+=1
            brier+=(p-y)**2
            logloss-=y*math.log(p)+(1-y)*math.log1p(-p)
    n=len(rows)
    return {
        "mae_f":round(mae/n,6),
        "crps":round(crps/n,6),
        "coverage_90":round(covered/n,6),
        "market_hours":verified,
        "brier":round(brier/verified,6) if verified else None,
        "logloss":round(logloss/verified,6) if verified else None,
    }


def _fit(rows: list[dict]) -> tuple[tuple[float,float,float],float,float]:
    """Small, predeclared convex grid, selected *only* on training CRPS."""
    proposals=[]
    n=len(rows)
    for i in range(5):
        for j in range(5-i):
            weights=(i/4,j/4,(4-i-j)/4)
            errors=[r["observed_f"]-_estimate(r,weights,0.) for r in rows]
            bias=max(-2.,min(2.,statistics.median(errors)*(n/(n+32.))))
            rms=math.sqrt(sum((err-bias)**2 for err in errors)/n)
            reference=math.sqrt(sum(r["sigma_f"]**2 for r in rows)/n)
            scale=max(.75,min(1.8,rms/reference))
            s=_metrics(rows,weights,bias,scale)
            # Tie-break in favor of less physics complexity and stable baseline.
            proposals.append((s["crps"],s["mae_f"],1-weights[0],
                              weights,round(bias,6),round(scale,6)))
    _,_,_,weights,bias,scale=min(proposals)
    return weights,bias,scale


def select_candidate(rows: list[dict]) -> dict:
    """Select with strictly older training and held-out later hours."""
    ordered=sorted(rows,key=lambda r:r["target_utc"])
    n=len(ordered)
    decision={
        "candidate_ready":False,"status":"INSUFFICIENT_PAIRED_HOURS",
        "history_hours":n,"train_hours":0,"validation_hours":0,
        "weights":[1.,0.,0.],"bias_f":0.,"sigma_scale":1.,
        "validation":None,
    }
    if n<MIN_TRAIN+VALIDATION:
        return decision
    train=ordered[:-VALIDATION]
    holdout=ordered[-VALIDATION:]
    weights,bias,scale=_fit(train)
    baseline=_metrics(holdout)
    candidate=_metrics(holdout,weights,bias,scale)
    decision.update({
        "train_hours":len(train),"validation_hours":len(holdout),
        "weights":list(weights),"bias_f":bias,"sigma_scale":scale,
        "validation":{
            "baseline_mae_f":baseline["mae_f"],
            "candidate_mae_f":candidate["mae_f"],
            "baseline_crps":baseline["crps"],
            "candidate_crps":candidate["crps"],
            "baseline_90_coverage":baseline["coverage_90"],
            "candidate_90_coverage":candidate["coverage_90"],
        },
    })
    if (candidate["mae_f"]<=baseline["mae_f"]-.05 and
        candidate["crps"]<=baseline["crps"]-.03 and
        candidate["mae_f"]<=2.5 and
        candidate["coverage_90"]>=.78 and
        candidate["coverage_90"]>=baseline["coverage_90"]-.15):
        decision["candidate_ready"]=True
        decision["status"]="HISTORICAL_POINT_HOLDOUT_IMPROVED"
    else:
        decision["status"]="HISTORICAL_HOLDOUT_NOT_IMPROVED"
    return decision


def evaluate_lockbox(lock: dict, cases: list[dict]) -> dict:
    """First *32 subsequent* distinct outcomes; fixed locked model only."""
    output={"enabled":False,"status":"WAITING_PROSPECTIVE_LOCKBOX",
            "lockbox_hours":0,"market_verified_hours":0,"metrics":None,
            "weights":lock.get("weights",[1.,0.,0.]),
            "bias_f":lock.get("bias_f",0.),
            "sigma_scale":lock.get("sigma_scale",1.)}
    if not lock.get("candidate_ready"):
        output["status"]="CANDIDATE_NOT_LOCKED"
        return output
    try:
        locked_at=_utc(lock["locked_at"])
        # Fixed 32 *calendar* hours after lock. Missing NOAA/index hours
        # cannot be replaced with later, easier conditions.
        first=locked_at.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
        expected=[first+timedelta(hours=i) for i in range(LOCKBOX)]
        cases_by_hour={}
        newest=None
        for row in cases:
            when=_utc(row["target_utc"])
            if when<=locked_at:
                continue
            newest=when if newest is None else max(newest,when)
            if first<=when<=expected[-1]:
                if when in cases_by_hour:
                    output["status"]="DUPLICATE_PROSPECTIVE_TARGET"
                    return output
                cases_by_hour[when]=row
        unique=[cases_by_hour[hour] for hour in expected
                if hour in cases_by_hour]
        output["lockbox_hours"]=len(unique)
        output["market_verified_hours"]=sum(bool(r.get("market_verified") and
                          r.get("threshold_f") is not None) for r in unique)
        if len(unique)<LOCKBOX:
            if newest is not None and newest>expected[-1]:
                output["status"]="INCOMPLETE_FIXED_LOCKBOX_WINDOW"
            return output
        if output["market_verified_hours"]<MIN_LOCKBOX_MARKETS:
            output["status"]="INSUFFICIENT_SETTLEMENT_VERIFIED_MARKETS"
            return output
        baseline=_metrics(unique)
        candidate=_metrics(unique,lock["weights"],lock["bias_f"],
                           lock["sigma_scale"])
        output["metrics"]={
            "baseline_mae_f":baseline["mae_f"],
            "candidate_mae_f":candidate["mae_f"],
            "baseline_crps":baseline["crps"],
            "candidate_crps":candidate["crps"],
            "baseline_brier":baseline["brier"],
            "candidate_brier":candidate["brier"],
            "baseline_logloss":baseline["logloss"],
            "candidate_logloss":candidate["logloss"],
            "baseline_90_coverage":baseline["coverage_90"],
            "candidate_90_coverage":candidate["coverage_90"],
        }
        if (candidate["mae_f"]<=baseline["mae_f"]-.03 and
            candidate["crps"]<=baseline["crps"]-.02 and
            candidate["mae_f"]<=2.5 and
            candidate["coverage_90"]>=.75 and
            candidate["coverage_90"]>=baseline["coverage_90"]-.15 and
            candidate["brier"]<=baseline["brier"]-.005 and
            candidate["logloss"]<=baseline["logloss"]-.005):
            output["enabled"]=True
            output["status"]="PROSPECTIVE_LOCKBOX_IMPROVED_NOT_CERTIFIED"
        else:
            output["status"]="PROSPECTIVE_LOCKBOX_NOT_IMPROVED"
    except (ValueError,KeyError,TypeError,OverflowError,ZeroDivisionError):
        output["status"]="INVALID_PROSPECTIVE_EVIDENCE"
    return output


def apply_locked_blend(baseline_f: float,sigma_f: float,hrrr_f: float,
                       nbm_f: float,decision: dict) -> tuple[float,float]:
    if not decision.get("enabled"):
        return baseline_f,sigma_f
    row={"baseline_f":_finite(baseline_f),"hrrr_f":_finite(hrrr_f),
         "nbm_f":_finite(nbm_f)}
    sigma=_finite(sigma_f,.01,30.)
    weights=decision["weights"]
    if len(weights)!=3 or any(not math.isfinite(w) or not 0<=w<=1 for w in weights):
        return baseline_f,sigma_f
    if abs(sum(weights)-1)>1e-6:
        return baseline_f,sigma_f
    try:
        bias=_finite(decision["bias_f"],-2.000001,2.000001)
        scale=_finite(decision["sigma_scale"],.749999,1.800001)
    except (ValueError,TypeError,KeyError):
        return baseline_f,sigma_f
    return _estimate(row,weights,bias),sigma*scale


def freeze_pair(conn: sqlite3.Connection,prediction: dict,
                asof: datetime) -> bool:
    """Immutable forecast-before-outcome pair. Works without listed contracts."""
    try:
        now=_utc(asof)
        if (_utc(prediction["asof"])!=now or
            prediction["source"]!="KALSHI_WEATHER_INDEX"):
            return False
        target=_utc(prediction["target_utc"])
        bucket=lead_bucket((target-now).total_seconds()/60)
        if bucket is None:
            return False
        city=prediction["city"]
        cfg=prediction["source_config_version"]
        physics=prediction["physical_ensemble"]
        if (not isinstance(cfg,str) or not cfg or
            physics.get("status")!="RESEARCH_ONLY_NOT_PROMOTED" or
            physics.get("calibration_version")!=cfg or
            (physics.get("forecast_target_utc") and
             _utc(physics["forecast_target_utc"])!=target)):
            return False
        event_ms=prediction["source_event_ms"]
        index_receipt=_utc(prediction["source_received_at"])
        if (not isinstance(event_ms,int) or isinstance(event_ms,bool) or
            event_ms>int(now.timestamp()*1000) or
            index_receipt>now or
            index_receipt<datetime.fromtimestamp(event_ms/1000,tz=timezone.utc)):
            return False
        receipts=physics["model_receipts"]
        runs=physics["model_runs"]
        for model in ("HRRR","NBM"):
            received=_utc(receipts[model])
            run=_utc(runs[model])
            if not run<=received<=now<target:
                return False
        base=_finite(prediction["pre_physical_mean_f"])
        sigma=_finite(prediction["pre_physical_sigma_f"],.01,30.)
        hrrr=_finite(physics["hrrr_index_f"])
        nbm=_finite(physics["nbm_index_f"])
        markets=prediction.get("markets") or []
        eligible=[m for m in markets if isinstance(m,dict) and
                 isinstance(m.get("ticker"),str) and
                 isinstance(m.get("threshold_f"),(int,float)) and
                 isinstance(m.get("probability_yes"),(int,float)) and
                 0<=m["probability_yes"]<=1 and
                 (not m.get("close_time_utc") or _utc(m["close_time_utc"])>now)]
        chosen=min(eligible,key=lambda m:(abs(m["probability_yes"]-.5),
                                         m["threshold_f"])) if eligible else None
        row={"city":city,"target_utc":target.isoformat(),
             "lead_minutes":bucket,"asof":now.isoformat(),
             "config_version":cfg,
             "baseline_f":base,"sigma_f":sigma,
             "hrrr_f":hrrr,"nbm_f":nbm,
             "market_ticker":chosen["ticker"] if chosen else None,
             "threshold_f":_finite(chosen["threshold_f"]) if chosen else None,
             "event_ticker":prediction.get("event_ticker"),
             "source_event_ms":event_ms,
             "source_received_at":index_receipt.isoformat(),
             "model_receipts":{m:_utc(receipts[m]).isoformat()
                               for m in ("HRRR","NBM")},
             "model_runs":{m:_utc(runs[m]).isoformat()
                           for m in ("HRRR","NBM")}}
        frozen=_canonical(row)
        _init(conn)
        result=conn.execute("""INSERT OR IGNORE INTO weather_hourly_physical_pairs(
                city,target_utc,lead_minutes,asof,config_version,frozen_json,frozen_sha)
                VALUES(?,?,?,?,?,?,?)""",
                (city,target.isoformat(),bucket,now.isoformat(),cfg,
                 frozen,_sha(frozen)))
        return result.rowcount==1
    except (ValueError,TypeError,KeyError,OverflowError,sqlite3.Error):
        return False


def journal_physical_pairs(db_path: Path, *, asof: datetime | None = None) -> dict:
    """Run from ordinary kalshiedge start; never wait for a Kalshi market."""
    from .hourly import CITIES, _forecast
    now=_utc(asof or datetime.now(timezone.utc))
    result={"mode":"HOURLY_PHYSICAL_EVIDENCE","inserted":0,
            "real_orders_enabled":False}
    path=Path(db_path)
    if not path.is_file():
        return result
    with closing(sqlite3.connect(path,timeout=10)) as conn:
        _init(conn)
        for city in CITIES:
            # The physical ensemble always targets the next official index
            # hour, even when the only currently listed Kalshi event is later.
            row=_forecast(conn,city,now,force_next_index_hour=True)
            result["inserted"]+=int(freeze_pair(conn,row,now))
        conn.commit()
    return result


def collect_cases(conn: sqlite3.Connection, city: str,
                  config_version: str, lead_minutes: int,
                  asof: datetime) -> list[dict]:
    """Only original frozen pairs and exact, promptly received official index labels."""
    now=_utc(asof)
    try:
        rows=conn.execute("""SELECT target_utc,asof,frozen_json,frozen_sha
          FROM weather_hourly_physical_pairs
          WHERE city=? AND config_version=? AND lead_minutes=? AND target_utc<?
          ORDER BY target_utc DESC LIMIT ?""",
          (city,config_version,lead_minutes,
           (now-timedelta(minutes=10)).isoformat(),MAX_CASES)).fetchall()
    except sqlite3.Error:
        return []
    if not rows:
        return []

    from .hourly_learning import collect_verified_cases
    verified,status=collect_verified_cases(conn,city,now)
    if status is not None:
        # Bad original settled evidence must never authorize promotion.
        verified=[]
    matched={r["target_utc"]:r["observed_index_f"] for r in verified}
    result=[]
    try:
        for target_raw,at_raw,raw,digest in rows:
            if not isinstance(raw,str) or _sha(raw)!=digest:
                return []
            frozen=json.loads(raw)
            target=_utc(target_raw)
            forecast_time=_utc(at_raw)
            if (frozen["city"]!=city or frozen["config_version"]!=config_version or
                frozen["lead_minutes"]!=lead_minutes or
                frozen["target_utc"]!=target_raw or
                frozen["asof"]!=at_raw or
                not forecast_time<target<now):
                return []
            if _utc(frozen["source_received_at"])>forecast_time:
                return []
            for model in ("HRRR","NBM"):
                if not (_utc(frozen["model_runs"][model])<=
                        _utc(frozen["model_receipts"][model])<=forecast_time):
                    return []
            event_ms=int(target.timestamp()*1000)
            reading=conn.execute("""SELECT received_at,temp_f,status,config_version,
                    source_sha,payload FROM weather_hourly_index
                    WHERE city=? AND event_ms=?""",(city,event_ms)).fetchone()
            if reading is None:
                continue
            received,temp,kind,cfg,source_sha,payload=reading
            label_received=_utc(received)
            if (cfg!=config_version or kind!="normal" or
                label_received<target or label_received>target+timedelta(minutes=10)
                or label_received>now):
                continue
            source=json.loads(payload)
            if (not isinstance(source_sha,str) or len(source_sha)!=64 or
                not isinstance(source,dict) or source.get("t")!=event_ms or
                source.get("status")!=kind or
                not isinstance(source.get("contributors"),int) or
                source.get("contributors",0)<1 or
                abs(float(source["v"])-float(temp))>1e-6):
                return []
            values={name:_finite(frozen[name]) for name in (
                    "baseline_f","hrrr_f","nbm_f")}
            sigma=_finite(frozen["sigma_f"],.01,30.)
            actual=_finite(temp)
            threshold=frozen.get("threshold_f")
            if threshold is not None:
                threshold=_finite(threshold)
            is_verified=(threshold is not None and
                         target_raw in matched and
                         abs(float(matched[target_raw])-actual)<=1e-6)
            result.append({"target_utc":target_raw,
                **values,"sigma_f":sigma,"observed_f":actual,
                "threshold_f":threshold,"market_verified":is_verified,
                "config_version":config_version})
    except (ValueError,TypeError,KeyError,OverflowError,
            json.JSONDecodeError,sqlite3.Error,AttributeError):
        return []
    return list(reversed(result))


def freeze_candidate_locks(db_path: Path, *,
                           asof: datetime | None = None) -> dict:
    """Lock first historical winner; later data can *only* test, not refit it."""
    now=_utc(asof or datetime.now(timezone.utc))
    result={"mode":"HOURLY_PHYSICAL_EVIDENCE","locked":0,
            "real_orders_enabled":False}
    path=Path(db_path)
    if not path.is_file():
        return result
    with closing(sqlite3.connect(path,timeout=10)) as conn:
        _init(conn)
        keys=conn.execute("""SELECT DISTINCT city,config_version,lead_minutes
            FROM weather_hourly_physical_pairs""").fetchall()
        for city,cfg,lead in keys:
            if conn.execute("""SELECT 1 FROM weather_hourly_physical_locks
                WHERE city=? AND config_version=? AND lead_minutes=?""",
                (city,cfg,lead)).fetchone():
                continue
            rows=collect_cases(conn,city,cfg,lead,now)
            decision=select_candidate(rows)
            if not decision["candidate_ready"]:
                continue
            params={**decision,"city":city,"config_version":cfg,
                    "lead_minutes":lead,"locked_at":now.isoformat()}
            frozen=_canonical(params)
            written=conn.execute("""INSERT OR IGNORE INTO weather_hourly_physical_locks
                (city,config_version,lead_minutes,locked_at,params_json,params_sha)
                VALUES(?,?,?,?,?,?)""",
                (city,cfg,lead,now.isoformat(),frozen,_sha(frozen)))
            result["locked"]+=written.rowcount
        conn.commit()
    return result


def physical_live_decision(conn: sqlite3.Connection,city: str,
                           config_version: str,target: datetime,
                           asof: datetime,physics: dict) -> dict:
    """Read-only eligibility: no recalibration or candidate mutation in UI path."""
    output={"enabled":False,"status":"COLLECTING_PAIRED_HOURS",
            "paired_hours":0,"lockbox_hours":0,
            "market_verified_hours":0,"metrics":None}
    now=_utc(asof)
    lead=lead_bucket((_utc(target)-now).total_seconds()/60)
    if lead is None:
        output["status"]="UNSUPPORTED_PHYSICAL_LEAD_TIME"
        return output
    if (physics.get("status")!="RESEARCH_ONLY_NOT_PROMOTED" or
        physics.get("calibration_version")!=config_version):
        output["status"]="NO_MATCHING_PHYSICAL_GUIDANCE"
        return output
    try:
        row=conn.execute("""SELECT locked_at,params_json,params_sha
             FROM weather_hourly_physical_locks
             WHERE city=? AND config_version=? AND lead_minutes=?""",
             (city,config_version,lead)).fetchone()
        if row is None:
            count=conn.execute("""SELECT COUNT(*) FROM weather_hourly_physical_pairs
              WHERE city=? AND config_version=? AND lead_minutes=?""",
              (city,config_version,lead)).fetchone()
            output["paired_hours"]=count[0] if count else 0
            return output
        locked_at,raw,digest=row
        if _sha(raw)!=digest:
            output["status"]="TAMPERED_PHYSICAL_MODEL_LOCK"
            return output
        model=json.loads(raw)
        if (model.get("city")!=city or
            model.get("config_version")!=config_version or
            model.get("lead_minutes")!=lead or
            model.get("locked_at")!=locked_at or _utc(locked_at)>now or
            model.get("candidate_ready") is not True or
            model.get("status")!="HISTORICAL_POINT_HOLDOUT_IMPROVED" or
            model.get("train_hours",0)<MIN_TRAIN or
            model.get("validation_hours")!=VALIDATION):
            output["status"]="INVALID_PHYSICAL_MODEL_LOCK"
            return output
        weights=model.get("weights")
        if (not isinstance(weights,list) or len(weights)!=3 or
            any(not isinstance(w,(int,float)) or isinstance(w,bool) or
                not math.isfinite(w) or not 0<=w<=1 for w in weights) or
            abs(sum(weights)-1)>1e-6 or
            not isinstance(model.get("bias_f"),(int,float)) or
            not -2<=model["bias_f"]<=2 or
            not isinstance(model.get("sigma_scale"),(int,float)) or
            not .75<=model["sigma_scale"]<=1.8):
            output["status"]="INVALID_PHYSICAL_MODEL_LOCK"
            return output
        cases=collect_cases(conn,city,config_version,lead,now)
        score=evaluate_lockbox(model,cases)
        output.update(score)
        output["paired_hours"]=len(cases)
        output["lead_minutes"]=lead
        return output
    except (sqlite3.Error,ValueError,TypeError,KeyError,
            json.JSONDecodeError,OverflowError):
        output["status"]="PHYSICAL_MODEL_AUDIT_ERROR"
        return output
