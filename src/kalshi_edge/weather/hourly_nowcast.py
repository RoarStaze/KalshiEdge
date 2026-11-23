"""Kalshi hourly Weather Index lead-time nowcast with chronological safety gates.

All features come from locally received, publisher-reported canonical minute
index records. The only label is the published top-of-hour value. No METAR
station, NWP grid, imagined forecast timestamp, market outcome or settlement
is substituted for the index. Historical holdout is research, not certification.
"""
from __future__ import annotations

import bisect
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from .predictor import normal_cdf
from .hourly_adaptive import fit_adaptive, predict_adaptive, validate_adaptive

ADAPTIVE_MODEL = "OBSERVATION_ADAPTIVE_V3"

LEAD_MINUTES = (5, 15, 30, 45, 60)
MIN_TRAIN = 48
HOLDOUT = 24
LOOKBACK_DAYS = 8
DATA_RECEIPT_LIMIT_SECONDS = 600
MODELS = {
    "PERSISTENCE": (0.0, 0.0),
    "FAST_TREND": (1.0, 0.0),
    "SLOW_TREND": (0.0, 0.9),
    "BLENDED_TREND": (0.65, 0.35),
    "DAMPED_TREND": (0.35, 0.40),
}


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("NAIVE_INDEX_TIME")
    return value.astimezone(timezone.utc)


def _load_points(conn: sqlite3.Connection, city: str, asof: datetime,
                 config_version: str) -> tuple[list[tuple[int,int,float,str]], str | None]:
    """Review original row and raw payload; exclude late receipts and old configs."""
    low=int((asof-timedelta(days=LOOKBACK_DAYS)).timestamp()*1000)
    high=int(asof.timestamp()*1000)
    try:
        rows=conn.execute("""SELECT event_ms,received_at,temp_f,status,
                             config_version,payload,source_sha
                  FROM weather_hourly_index
                  WHERE city=? AND event_ms>=? AND event_ms<=?
                  ORDER BY event_ms ASC LIMIT 15000""",(city,low,high)).fetchall()
    except sqlite3.Error:
        return [],"INVALID_INDEX_EVIDENCE"
    points=[]
    try:
        for event_ms,received_at,temp,status,cfg,payload,sha in rows:
            if not (isinstance(event_ms,int) and not isinstance(event_ms,bool)
                    and isinstance(cfg,str) and isinstance(sha,str) and sha
                    and isinstance(status,str) and status in ("normal","degraded")
                    and isinstance(temp,(int,float)) and math.isfinite(temp)
                    and -130<temp<160):
                raise ValueError("INDEX_DATA_INVALID")
            receipt=int(_utc(received_at).timestamp()*1000)
            raw=json.loads(payload)
            if (not isinstance(raw,dict) or raw.get("t")!=event_ms or
                raw.get("status")!=status or
                not isinstance(raw.get("contributors"),int) or
                isinstance(raw.get("contributors"),bool) or
                raw["contributors"]<=0 or
                not isinstance(raw.get("v"),(int,float)) or
                not math.isfinite(raw["v"]) or
                abs(float(raw["v"])-float(temp))>1e-6 or
                abs(temp-round(temp,2))>1e-6):
                raise ValueError("INDEX_ORIGINAL_PAYLOAD_CHANGED")
            if receipt<event_ms or receipt>high:
                # A future receipt is expected when data are inserted after a
                # historical asof; it must not be used as an observed feature.
                if receipt<event_ms:
                    raise ValueError("RECEIPT_BEFORE_EVENT")
                continue
            if cfg!=config_version:
                continue
            if receipt-event_ms>DATA_RECEIPT_LIMIT_SECONDS*1000:
                continue
            points.append((event_ms,receipt,float(temp),status))
    except (ValueError,TypeError,KeyError,OverflowError,json.JSONDecodeError,
            AttributeError):
        return [],"INVALID_INDEX_EVIDENCE"
    return points,None


def _select_before(points: list[tuple], times: list[int],
                   anchor_ms: int) -> list[tuple]:
    """Take only records genuinely available at the historical decision time."""
    end=bisect.bisect_right(times,anchor_ms)
    available=[]
    for point in reversed(points[max(0,end-82):end]):
        if point[1]<=anchor_ms:
            available.append(point)
    return available


def _case(points: list[tuple], times: list[int], target_ms: int,
          lead_minutes: int) -> dict | None:
    anchor=target_ms-lead_minutes*60000
    past=_select_before(points,times,anchor)
    if not past:
        return None
    newest=past[0]
    latest_ms,_,current,status=newest
    if (anchor-latest_ms)/60000>10:
        return None

    def older(min_gap: float, max_gap: float, preferred: float) -> tuple | None:
        options=[r for r in past[1:]
                 if min_gap<=(latest_ms-r[0])/60000<=max_gap]
        if not options:
            return None
        return min(options,key=lambda r:abs((latest_ms-r[0])/60000-preferred))

    old10=older(8,18,10)
    old30=older(24,42,30)
    oldbaseline=older(10,50,10)
    if old10 is None or old30 is None or oldbaseline is None:
        return None
    diff=lambda other:(current-other[2])/((latest_ms-other[0])/3600000)
    fast=max(-5.,min(5.,diff(old10)))
    slow=max(-5.,min(5.,diff(old30)))
    baseline_slope=max(-5.,min(5.,diff(oldbaseline)))
    hours=(target_ms-latest_ms)/3600000
    baseline=current+.55*baseline_slope*hours
    sigma=.8+.85*math.sqrt(max(0,hours))
    if status=="degraded":
        sigma+=.3
    # Target must be present, official and published in a timely manner.
    label_index=bisect.bisect_left(times,target_ms)
    if label_index>=len(times) or times[label_index]!=target_ms:
        return None
    label=points[label_index]
    if not (target_ms<=label[1]<=target_ms+600000):
        return None
    return {
        "target_utc":datetime.fromtimestamp(target_ms/1000,tz=timezone.utc).isoformat(),
        "forecast_asof":datetime.fromtimestamp(anchor/1000,tz=timezone.utc).isoformat(),
        "baseline_mean_f":baseline,"baseline_sigma_f":sigma,
        "current_index_f":current,"observed_index_f":label[2],
        "fast_slope":fast,"slow_slope":slow,"hours_ahead":hours,
    }


def index_cases(conn: sqlite3.Connection, city: str, asof: datetime,
                *, lead_minutes: int, config_version: str) -> list[dict]:
    """Recreate exactly one source-time-valid observation per completed hour."""
    asof=_utc(asof)
    if lead_minutes not in LEAD_MINUTES:
        return []
    points,error=_load_points(conn,city,asof,config_version)
    if error:
        raise ValueError(error)
    return _cases_from_points(points,asof,lead_minutes)


def _cases_from_points(points: list[tuple], asof: datetime,
                       lead_minutes: int) -> list[dict]:
    if not points:
        return []
    times=[r[0] for r in points]
    end=int((asof-timedelta(minutes=10)).timestamp()*1000)
    earliest=times[0]
    candidates=[]
    hour_ms=3600000
    first=((earliest//hour_ms)+1)*hour_ms
    for target_ms in range(first,end+1,hour_ms):
        case=_case(points,times,target_ms,lead_minutes)
        if case is not None:
            candidates.append(case)
    return candidates


def _estimate(row: dict, model: str, params: dict | None = None) -> float:
    if model==ADAPTIVE_MODEL:
        if not validate_adaptive(params):
            raise ValueError("INVALID_ADAPTIVE_NOWCAST_PARAMETERS")
        return predict_adaptive(
            row["current_index_f"],row["baseline_sigma_f"],
            row["fast_slope"],row["slow_slope"],row["hours_ahead"],
            params)[0]
    fast,slow=MODELS[model]
    return row["current_index_f"]+row["hours_ahead"]*(
        fast*row["fast_slope"]+slow*row["slow_slope"])


def _crps(mean: float, sigma: float, actual: float) -> float:
    # Exact Gaussian continuous ranked probability score: proper scoring
    # rule over *every* possible threshold, without mining market strikes.
    z=(actual-mean)/sigma
    phi=math.exp(-.5*z*z)/math.sqrt(2*math.pi)
    return sigma*(z*(2*normal_cdf(z,0.,1.)-1.)+
                  2*phi-1/math.sqrt(math.pi))


def _score(cases: list[dict], model: str | None, scale: float,
           params: dict | None = None) -> dict:
    total_mae=total_crps=0.
    covered=0
    for row in cases:
        mean=row["baseline_mean_f"] if model is None else _estimate(row,model,params)
        sigma=max(.05,row["baseline_sigma_f"]*scale)
        actual=row["observed_index_f"]
        total_mae+=abs(actual-mean)
        total_crps+=_crps(mean,sigma,actual)
        covered+=abs(actual-mean)<=1.64485362695*sigma
    n=len(cases)
    return {"mae_f":round(total_mae/n,6),"crps":round(total_crps/n,6),
            "coverage_90":round(covered/n,6)}


def _train_scale(cases: list[dict], model: str) -> float:
    rms=math.sqrt(sum((row["observed_index_f"]-_estimate(row,model))**2/
             row["baseline_sigma_f"]**2 for row in cases)/len(cases))
    return round(max(.75,min(1.65,rms)),6)


def choose_index_nowcast(cases: list[dict]) -> dict:
    """Train on the older hours; keep the last 24 hours untouched for gating."""
    ordered=sorted(cases,key=lambda x:x["target_utc"])
    count=len(ordered)
    base={"enabled":False,"status":"INSUFFICIENT_INDEX_HOURS",
          "history_hours":count,"training_hours":0,"validation_hours":0,
          "model":None,"sigma_scale":1.,"parameters":None,"validation":None}
    if count<MIN_TRAIN+HOLDOUT:
        return base
    train=ordered[:-HOLDOUT]
    valid=ordered[-HOLDOUT:]
    alternatives=[]
    for name in MODELS:
        scale=_train_scale(train,name)
        scores=_score(train,name,scale)
        alternatives.append((scores["crps"],scores["mae_f"],name,scale,None))
    # Regularized regression is a *candidate* alongside static trends.
    # All coefficients and sigma are fitted on older hours ONLY.
    try:
        params=fit_adaptive(train)
        if validate_adaptive(params):
            score=_score(train,ADAPTIVE_MODEL,params["sigma_scale"],params)
            alternatives.append((score["crps"],score["mae_f"],
                                 ADAPTIVE_MODEL,params["sigma_scale"],params))
    except (ValueError,TypeError,OverflowError,ZeroDivisionError):
        pass
    _,_,name,scale,params=min(alternatives,key=lambda r:(r[0],r[1],r[2]))
    baseline=_score(valid,None,1.)
    challenger=_score(valid,name,scale,params)
    base.update({"training_hours":len(train),"validation_hours":len(valid),
                 "model":name,"sigma_scale":scale,"parameters":params,
                 "validation":{
                   "baseline_mae_f":baseline["mae_f"],
                   "candidate_mae_f":challenger["mae_f"],
                   "baseline_crps":baseline["crps"],
                   "candidate_crps":challenger["crps"],
                   "baseline_90_coverage":baseline["coverage_90"],
                   "candidate_90_coverage":challenger["coverage_90"]}})
    if (challenger["mae_f"]<=baseline["mae_f"]-.05 and
        challenger["crps"]<=baseline["crps"]-.03 and
        challenger["mae_f"]<=2.5 and
        challenger["coverage_90"]>=.72 and
        challenger["coverage_90"]>=baseline["coverage_90"]-.15):
        base["enabled"]=True
        base["status"]="HISTORICAL_INDEX_HOLDOUT_IMPROVED_UNCERTIFIED"
    else:
        base["status"]="INDEX_HOLDOUT_NOT_IMPROVED"
    return base


def nowcast_prediction(current_f: float, baseline_sigma_f: float,
                       fast_slope: float, slow_slope: float,
                       hours_ahead: float, decision: dict) -> tuple[float,float]:
    if not decision.get("enabled"):
        return current_f,baseline_sigma_f
    if decision.get("model")==ADAPTIVE_MODEL:
        return predict_adaptive(
            current_f,baseline_sigma_f,fast_slope,slow_slope,hours_ahead,
            decision.get("parameters"))
    row={"current_index_f":current_f,"fast_slope":fast_slope,
         "slow_slope":slow_slope,"hours_ahead":hours_ahead}
    return _estimate(row,decision["model"]),baseline_sigma_f*decision["sigma_scale"]


def index_nowcast_decision(conn: sqlite3.Connection, city: str,
                           asof: datetime, *, lead_minutes: int,
                           config_version: str) -> dict:
    fail={"enabled":False,"status":"UNSUPPORTED_LEAD_TIME",
          "history_hours":0,"training_hours":0,"validation_hours":0,
          "model":None,"sigma_scale":1.,"validation":None,
          "live_slopes":None}
    if lead_minutes not in LEAD_MINUTES:
        return fail
    try:
        now=_utc(asof)
        points,error=_load_points(conn,city,now,config_version)
        if error:
            return {**fail,"status":error}
        samples=_cases_from_points(points,now,lead_minutes)
        decision=choose_index_nowcast(samples)
        decision["live_slopes"]=_slopes_from_points(points,now)
        return decision
    except (ValueError,sqlite3.Error):
        return {**fail,"status":"INVALID_INDEX_EVIDENCE"}


def _slopes_from_points(points: list[tuple],
                        asof: datetime) -> tuple[float,float,float] | None:
    """Live features follow exactly the historical local-receipt cutoff."""
    if not points:
        return None
    times=[r[0] for r in points]
    past=_select_before(points,times,int(asof.timestamp()*1000))
    if not past:
        return None
    newest=past[0]
    if (asof.timestamp()*1000-newest[0])/60000>10:
        return None
    def pick(gap,min_gap,max_gap):
        candidates=[r for r in past[1:]
                    if min_gap<=(newest[0]-r[0])/60000<=max_gap]
        if not candidates:
            return None
        return min(candidates,key=lambda r:abs((newest[0]-r[0])/60000-gap))
    old10=pick(10,8,18)
    old30=pick(30,24,42)
    if old10 is None or old30 is None:
        return None
    def slope(old):
        value=(newest[2]-old[2])/((newest[0]-old[0])/3600000)
        return max(-5.,min(5.,value))
    return slope(old10),slope(old30),newest[2],newest[0]


def current_slopes(conn: sqlite3.Connection,city: str,asof: datetime,
                   *, config_version: str) -> tuple[float,float,float] | None:
    now=_utc(asof)
    points,error=_load_points(conn,city,now,config_version)
    return None if error else _slopes_from_points(points,now)
