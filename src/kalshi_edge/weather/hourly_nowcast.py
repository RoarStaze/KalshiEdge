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
from .hourly_hybrid import predict_hybrid, validate_hybrid
from .hourly_v14 import V14_MODEL, predict_v14
from .hourly_volatility import (
    MIN_TRAIN as VOLATILITY_MIN_TRAIN, fit_volatility,
    predict_volatility, validate_volatility,
)

ADAPTIVE_MODEL = "OBSERVATION_ADAPTIVE_V3"
VOLATILITY_MODEL = "OBSERVATION_REGIME_SIGMA_V4"
DIURNAL_MODEL = "DIURNAL_ANALOGUE_V5"
HYBRID_MODEL = "REGULARIZED_HOURLY_HYBRID_V13"

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


def _diurnal_delta(points: list[tuple], times: list[int],
                   anchor_ms: int, target_ms: int) -> float | None:
    """Prior-day same-clock temperature *change*, never same-day future.

    The exact same receipt cutoff is used in historical replay and live.
    Data must be published promptly and actually available at decision time.
    """
    if not points or not times or anchor_ms>=target_ms:
        return None
    day=24*3600000

    def before(ms: int) -> float | None:
        pos=bisect.bisect_right(times,ms)
        if pos<=0:
            return None
        for item in reversed(points[max(0,pos-4):pos]):
            event,receipt,temp,status=item
            if (0<=ms-event<=8*60000 and
                0<=receipt-event<=DATA_RECEIPT_LIMIT_SECONDS*1000 and
                receipt<=anchor_ms and status=="normal" and
                math.isfinite(temp)):
                return float(temp)
        return None

    # Two previous calendar days; never use the target hour's actual value
    # as a feature. Bound drift to limit sensitivity to outlier index jumps.
    changes=[]
    for lag in (1,2):
        old_start=before(anchor_ms-lag*day)
        old_end=before(target_ms-lag*day)
        if old_start is not None and old_end is not None:
            changes.append(max(-6.,min(6.,old_end-old_start)))
    if not changes:
        return None
    return round(sum(changes)/len(changes),6)


def _diurnal_params(rows: list[dict]) -> dict:
    deltas=[float(r["diurnal_delta_f"]) for r in rows]
    # Ridge shrinks the analogue toward no correction on limited evidence.
    numerator=sum(d*(r["observed_index_f"]-r["current_index_f"])
                  for d,r in zip(deltas,rows))
    gain=max(-1.,min(1.,numerator/(sum(d*d for d in deltas)+6.)))
    residuals=[(r["observed_index_f"]-r["current_index_f"]-gain*d)/
               r["baseline_sigma_f"] for r,d in zip(rows,deltas)]
    rms=math.sqrt(sum(x*x for x in residuals)/len(residuals))
    scale=max(.75,min(1.65,rms))
    return {"gain":round(gain,6),"sigma_scale":round(scale,6)}


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
        "label_received_at":datetime.fromtimestamp(
            label[1]/1000,tz=timezone.utc).isoformat(),
        "fast_slope":fast,"slow_slope":slow,"hours_ahead":hours,
        "diurnal_delta_f":_diurnal_delta(points,times,anchor,target_ms),
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
    if model==DIURNAL_MODEL:
        if (not isinstance(params,dict) or
            not isinstance(params.get("gain"),(int,float)) or
            not math.isfinite(params["gain"]) or
            abs(params["gain"])>1. or
            not isinstance(row.get("diurnal_delta_f"),(int,float)) or
            not math.isfinite(row["diurnal_delta_f"])):
            raise ValueError("INVALID_DIURNAL_ANALOGUE_FEATURES")
        return row["current_index_f"]+params["gain"]*row["diurnal_delta_f"]
    if model==VOLATILITY_MODEL:
        if not validate_volatility(params):
            raise ValueError("INVALID_VOLATILITY_NOWCAST_PARAMETERS")
        return predict_volatility(
            row["current_index_f"], row["baseline_sigma_f"],
            row["fast_slope"], row["slow_slope"], row["hours_ahead"],
            params)[0]
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
        if model==VOLATILITY_MODEL:
            mean,sigma=predict_volatility(
                row["current_index_f"],row["baseline_sigma_f"],
                row["fast_slope"],row["slow_slope"],row["hours_ahead"],params)
        else:
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
    # Preserve the prior V7 training champion before introducing an
    # uncertainty-only challenger. A failed V8 candidate must not disable
    # the valid predecessor just because it won on older training hours.
    incumbent_choice=min(alternatives,key=lambda r:(r[0],r[1],r[2]))
    # Separate probabilistic candidate: same adaptive point mean with
    # uncertainty rising only when fast and slow index trends disagree.
    # It must have both calm and divergent training hours, then beat the
    # incumbent on the untouched holdout before changing live probabilities.
    if len(train)>=VOLATILITY_MIN_TRAIN:
        try:
            volatility=fit_volatility(train)
            if validate_volatility(volatility):
                scores=_score(train,VOLATILITY_MODEL,
                              volatility["sigma_scale"],volatility)
                alternatives.append((scores["crps"],scores["mae_f"],
                                     VOLATILITY_MODEL,volatility["sigma_scale"],
                                     volatility))
        except (ValueError,TypeError,OverflowError,ZeroDivisionError):
            pass

    # V12: same-clock prior-day changes can capture repeatable diurnal
    # temperature movement. Candidate competes only after enough *earlier*
    # examples and is compared with the V7 incumbent on the identical
    # untouched 24-hour holdout. No retrospective promotion on the user's
    # Weather History percentage.
    usable_train=[r for r in train if isinstance(r.get("diurnal_delta_f"),
                  (int,float)) and math.isfinite(r["diurnal_delta_f"]) and
                  r["baseline_sigma_f"]>0]
    if (len(usable_train)>=80 and len(valid)==HOLDOUT and
        all(isinstance(r.get("diurnal_delta_f"),(int,float)) and
            math.isfinite(r["diurnal_delta_f"]) and
            r["baseline_sigma_f"]>0 for r in valid) and
        sum(abs(r["diurnal_delta_f"])>=.35 for r in usable_train)>=24):
        params=_diurnal_params(usable_train)
        candidate_train=_score(usable_train,DIURNAL_MODEL,
                               params["sigma_scale"],params)
        _,_,old_name,old_scale,old_params=incumbent_choice
        incumbent_train=_score(usable_train,old_name,old_scale,old_params)
        if (candidate_train["mae_f"]<=incumbent_train["mae_f"]-.03 and
            candidate_train["crps"]<=incumbent_train["crps"]-.02):
            alternatives.append((candidate_train["crps"],
                                 candidate_train["mae_f"],DIURNAL_MODEL,
                                 params["sigma_scale"],params))

    _,_,name,scale,params=min(alternatives,key=lambda r:(r[0],r[1],r[2]))
    baseline=_score(valid,None,1.)
    challenger=_score(valid,name,scale,params)
    if name==DIURNAL_MODEL:
        _,_,old_name,old_scale,old_params=incumbent_choice
        old_scores=_score(valid,old_name,old_scale,old_params)
        if (challenger["mae_f"]>old_scores["mae_f"]-.05 or
            challenger["crps"]>old_scores["crps"]-.02 or
            challenger["coverage_90"]<old_scores["coverage_90"]-.10):
            name,scale,params=old_name,old_scale,old_params
            challenger=old_scores
    if name==VOLATILITY_MODEL:
        # A new sigma model is promoted only when it improves the same
        # untouched held-out hours against the V7 incumbent, not merely
        # the untouched persistence baseline. Otherwise keep V7's result.
        _,_,old_name,old_scale,old_params=incumbent_choice
        old_scores=_score(valid,old_name,old_scale,old_params)
        if (challenger["crps"]>old_scores["crps"]-.01 or
            challenger["coverage_90"]<old_scores["coverage_90"]-.15):
            name,scale,params=old_name,old_scale,old_params
            challenger=old_scores
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
                       hours_ahead: float, decision: dict, *,
                       diurnal_delta_f: float | None = None) -> tuple[float,float]:
    if not decision.get("enabled"):
        return current_f,baseline_sigma_f
    if decision.get("model")==V14_MODEL:
        if diurnal_delta_f is None:
            return current_f,baseline_sigma_f
        row={"current_index_f":current_f,
             "baseline_sigma_f":baseline_sigma_f,
             "fast_slope":fast_slope,"slow_slope":slow_slope,
             "hours_ahead":hours_ahead,
             "diurnal_delta_f":diurnal_delta_f}
        return predict_v14(row,decision.get("parameters"))
    if decision.get("model")==HYBRID_MODEL:
        if diurnal_delta_f is None:
            return current_f,baseline_sigma_f
        row={"current_index_f":current_f,
             "baseline_sigma_f":baseline_sigma_f,
             "fast_slope":fast_slope,"slow_slope":slow_slope,
             "hours_ahead":hours_ahead,
             "diurnal_delta_f":diurnal_delta_f}
        return predict_hybrid(row,decision.get("parameters"))
    if decision.get("model")==DIURNAL_MODEL:
        if diurnal_delta_f is None:
            return current_f,baseline_sigma_f
        row={"current_index_f":current_f,"diurnal_delta_f":diurnal_delta_f}
        params=decision.get("parameters")
        return _estimate(row,DIURNAL_MODEL,params),baseline_sigma_f*decision["sigma_scale"]
    if decision.get("model")==VOLATILITY_MODEL:
        return predict_volatility(
            current_f,baseline_sigma_f,fast_slope,slow_slope,hours_ahead,
            decision.get("parameters"))
    if decision.get("model")==ADAPTIVE_MODEL:
        return predict_adaptive(
            current_f,baseline_sigma_f,fast_slope,slow_slope,hours_ahead,
            decision.get("parameters"))
    row={"current_index_f":current_f,"fast_slope":fast_slope,
         "slow_slope":slow_slope,"hours_ahead":hours_ahead}
    return _estimate(row,decision["model"]),baseline_sigma_f*decision["sigma_scale"]


def index_nowcast_decision(conn: sqlite3.Connection, city: str,
                           asof: datetime, *, lead_minutes: int,
                           config_version: str,
                           target_utc: datetime | None = None) -> dict:
    fail={"enabled":False,"status":"UNSUPPORTED_LEAD_TIME",
          "history_hours":0,"training_hours":0,"validation_hours":0,
          "model":None,"sigma_scale":1.,"validation":None,
          "live_slopes":None,"live_diurnal_delta_f":None}
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
        times=[r[0] for r in points]
        ms=int(now.timestamp()*1000)
        exact_target=(_utc(target_utc) if target_utc is not None else
                      now+timedelta(minutes=lead_minutes))
        actual_target_ms=int(exact_target.timestamp()*1000)
        if (actual_target_ms<=ms or actual_target_ms-ms>3600000):
            return {**fail,"status":"INVALID_HOURLY_TARGET"}
        if decision.get("model")==DIURNAL_MODEL:
            decision["live_diurnal_delta_f"]=_diurnal_delta(
                points,times,ms,actual_target_ms)

        # Historical temperature-only promotion is separate from the
        # stricter market-outcome and live-trading certification gates.
        # Only exact local research files produced by the archive runner
        # can propose winners; live coefficients are refitted on genuinely
        # received Index observations from the CURRENT calibration version.
        try:
            from pathlib import Path
            from .hourly_promotion import (
                promote_local_nowcast, read_historical_promotions,
            )
            db_name=conn.execute("PRAGMA database_list").fetchone()[2]
            if db_name:
                policy=read_historical_promotions(
                    Path(db_name),city,lead_minutes,asof=now)
                decision["historical_temperature_backtest"]=policy
                if policy["model"] is not None:
                    delta=_diurnal_delta(points,times,ms,actual_target_ms)
                    decision=promote_local_nowcast(
                        decision,samples,
                        promoted_version=policy["model"],live_delta=delta)
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
            decision["historical_temperature_status"]="HISTORICAL_TEMPERATURE_POLICY_UNAVAILABLE"

        # The stronger full-pipeline market-verified promotion still takes
        # precedence when all independent frozen-market gates pass.
        # Promotion cannot be decided from training MAE or imagined
        # settlement: only the collector's saved, checksum-verified
        # three-block paired contest against V12 + exact verified Kalshi
        # YES/NO probabilities may authorize a V13 candidate.
        try:
            from pathlib import Path
            from .hourly_model_comparison import read_local_comparison
            name=conn.execute("PRAGMA database_list").fetchone()[2]
            cache=read_local_comparison(Path(name)) if name else {}
            match=next((g for g in cache.get("groups",[])
                        if g.get("city")==city and
                        g.get("lead_minutes")==lead_minutes and
                        g.get("config_version")==config_version and
                        g.get("eligible_for_live_promotion") is True and
                        g.get("matched_hours",0)>=72 and
                        g.get("frozen_live_hours",0)>=72 and
                        g.get("verified_market_hours",0)>=24),None)
            if (cache.get("status")=="RESEARCH_SCORED" and
                cache.get("any_promoted") is True and
                cache.get("asof_utc") and
                _utc(cache["asof_utc"])<=now and match is not None and
                validate_hybrid(match.get("parameters")) and
                decision["live_slopes"] is not None):
                live_delta=_diurnal_delta(
                    points,times,ms,actual_target_ms)
                if live_delta is not None:
                    # Keep the original incumbent's score metadata separate.
                    decision["incumbent_model"]=decision["model"]
                    decision["model"]=HYBRID_MODEL
                    decision["parameters"]=match["parameters"]
                    decision["sigma_scale"]=match["parameters"]["sigma_scale"]
                    decision["live_diurnal_delta_f"]=live_delta
                    decision["enabled"]=True
                    decision["status"]="PAIRED_MARKET_BACKTEST_IMPROVED_UNCERTIFIED"
                    decision["validation_hours"]=match["matched_hours"]
                    # Do not display the old V12-only validation as if it
                    # measured the selected V13 against the full live model.
                    decision["validation"]={
                        "baseline_mae_f":match["incumbent"]["mae_f"],
                        "candidate_mae_f":match["candidate"]["mae_f"],
                        "baseline_crps":match["incumbent"]["crps"],
                        "candidate_crps":match["candidate"]["crps"],
                        "baseline_90_coverage":match["incumbent"]["coverage_90"],
                        "candidate_90_coverage":match["candidate"]["coverage_90"],
                        "baseline_brier":match["incumbent"]["brier"],
                        "candidate_brier":match["candidate"]["brier"],
                        "frozen_full_live_hours":match["frozen_live_hours"],
                        "market_verified_hours":match["verified_market_hours"]}
                    decision["paired_comparison"]={
                        "status":match["status"],
                        "matched_hours":match["matched_hours"],
                        "verified_market_hours":match["verified_market_hours"],
                        "incumbent":match["incumbent"],
                        "candidate":match["candidate"]}
            else:
                decision["paired_comparison_status"]=(
                    match["status"] if match else cache.get("status",
                    "INSUFFICIENT_VERIFIED_MARKET_PAIRS"))
        except (ValueError,TypeError,KeyError,OSError,sqlite3.Error):
            # A stale, corrupted or absent cache never changes the incumbent.
            decision["paired_comparison_status"]="PAIRED_EVIDENCE_UNAVAILABLE"
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
