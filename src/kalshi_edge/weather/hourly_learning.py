"""Point-in-time, conservatively gated postprocessing for Kalshi hourly index.

Only source-reconciled *precommitted* hourly forecasts and their subsequently
verified settlements can enter training. A single chronological holdout must
improve point and binary-probability scores before deployment. This is not
a claim of demonstrated prospective accuracy or profitable trading.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from .predictor import normal_cdf

MIN_TRAIN = 64
HOLDOUT = 24
MAX_HISTORY = 300


def _metric_cases(cases: list[dict], bias: float, gain: float,
                  sigma_scale: float) -> dict:
    mae = brier = logloss = 0.0
    coverage_hits = 0
    for row in cases:
        mean, sigma = _transform(row["baseline_mean_f"], row["baseline_sigma_f"],
                                 row["current_index_f"], bias, gain, sigma_scale)
        actual = row["observed_index_f"]
        p = max(1e-8, min(1-1e-8, 1-normal_cdf(row["threshold_f"], mean, sigma)))
        y = row["yes"]
        mae += abs(mean-actual)
        coverage_hits += abs(mean-actual)<=1.64485362695*sigma
        brier += (p-y)**2
        logloss -= y*math.log(p) + (1-y)*math.log1p(-p)
    n = len(cases)
    return {"mae_f":round(mae/n,6),"brier":round(brier/n,6),
            "logloss":round(logloss/n,6),
            "coverage_90":round(coverage_hits/n,6)}


def _transform(mean: float, sigma: float, current: float,
               bias: float, gain: float, scale: float) -> tuple[float,float]:
    return mean+bias+gain*(mean-current),sigma*scale


def _fit(rows: list[dict]) -> tuple[float,float,float]:
    # Regularized linear adjustment to existing index momentum, with bias.
    # Never fit on the chronological validation tail.
    n=len(rows)
    x=[r["baseline_mean_f"]-r["current_index_f"] for r in rows]
    y=[r["observed_index_f"]-r["baseline_mean_f"] for r in rows]
    mx=sum(x)/n
    my=sum(y)/n
    denom=sum((t-mx)**2 for t in x)
    # Ridge dampens correction when there has been little trend variation.
    gain=max(-.75,min(.75,sum((x[i]-mx)*(y[i]-my)
                  for i in range(n))/(denom+10.0)))
    shrink=n/(n+24.0)
    gain*=shrink
    bias=max(-3.,min(3.,(my-gain*mx)*shrink))
    scaled_residuals=[(y[i]-bias-gain*x[i])/rows[i]["baseline_sigma_f"]
                      for i in range(n)]
    fitted_scale=math.sqrt(sum(e*e for e in scaled_residuals)/n)
    scale=max(.8,min(1.8,1+(fitted_scale-1)*(n/(n+60.0))))
    return round(bias,6),round(gain,6),round(scale,6)


def choose_correction(cases: list[dict]) -> dict:
    """Decide on a conservative observed-hour model, with no synthetic labels."""
    ordered=sorted(cases,key=lambda row:row["target_utc"])
    n=len(ordered)
    report={"enabled":False,"status":"INSUFFICIENT_VERIFIED_HOURS",
            "verified_hours":n,"training_hours":0,"validation_hours":0,
            "fitted_bias_f":0.,"fitted_trend_gain":0.,"fitted_sigma_scale":1.,
            "validation":None}
    if n<MIN_TRAIN+HOLDOUT:
        return report
    training=ordered[:-HOLDOUT]
    validation=ordered[-HOLDOUT:]
    bias,gain,scale=_fit(training)
    baseline=_metric_cases(validation,0.,0.,1.)
    candidate=_metric_cases(validation,bias,gain,scale)
    report.update({"training_hours":len(training),"validation_hours":len(validation),
                   "fitted_bias_f":bias,"fitted_trend_gain":gain,
                   "fitted_sigma_scale":scale,
                   "validation":{
                      "baseline_mae_f":baseline["mae_f"],
                      "candidate_mae_f":candidate["mae_f"],
                      "baseline_brier":baseline["brier"],
                      "candidate_brier":candidate["brier"],
                      "baseline_logloss":baseline["logloss"],
                      "candidate_logloss":candidate["logloss"],
                      "baseline_90_coverage":baseline["coverage_90"],
                      "candidate_90_coverage":candidate["coverage_90"],
                   }})
    # Selection on the held-out tail is research-grade evidence, not
    # independent prospective certification. Require joint improvements.
    if (candidate["mae_f"] <= baseline["mae_f"]-.03 and
        candidate["mae_f"] <= 2.5 and  # reject catastrophic regime drift
        candidate["coverage_90"] >= .75 and
        candidate["coverage_90"] >= baseline["coverage_90"]-.15 and
        candidate["brier"] <= baseline["brier"]-.003 and
        candidate["logloss"] <= baseline["logloss"]-.001):
        report["enabled"]=True
        report["status"]="HISTORICAL_HOLDOUT_IMPROVED_NOT_CERTIFIED"
    else:
        report["status"]="HOLDOUT_DID_NOT_IMPROVE"
    return report


def apply_correction(mean: float, sigma: float, current: float,
                     decision: dict) -> tuple[float,float]:
    if not decision.get("enabled"):
        return mean,sigma
    return _transform(mean,sigma,current,
                      decision["fitted_bias_f"],
                      decision["fitted_trend_gain"],
                      decision["fitted_sigma_scale"])


def _as_utc(value: str) -> datetime:
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("NAIVE_HOURLY_EVIDENCE_TIMESTAMP")
    return dt.astimezone(timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_verified_cases(conn: sqlite3.Connection, city: str,
                           asof: datetime) -> tuple[list[dict], str | None]:
    """Recheck source records; no future checks, unverifiable labels or hindsight.

    Returns empty cases and an explicit error if *any* eligible source fails
    its hash, temporal or settlement reconciliation. Never train from it.
    """
    from .hourly import parse_index, parse_markets  # avoid import cycle
    if asof.tzinfo is None or asof.utcoffset() is None:
        return [], "INVALID_VERIFIED_EVIDENCE"
    asof=asof.astimezone(timezone.utc)
    try:
        data=conn.execute("""SELECT
            s.event_ticker,s.market_ticker,s.asof,s.target_utc,
            s.source_reading_ms,s.source_receipt,s.forecast_json,s.forecast_sha,
            o.market_ticker,o.result,o.observed_index_f,o.observed_event_ms,
            o.checked_at,o.source_index_raw,o.source_index_sha,
            o.market_raw,o.market_sha
          FROM weather_hourly_snapshots s JOIN weather_hourly_settlements o
            ON o.city=s.city AND o.event_ticker=s.event_ticker
          WHERE s.city=? AND o.checked_at<=? AND s.target_utc<?
          ORDER BY s.target_utc DESC LIMIT ?""",
          (city,asof.isoformat(),asof.isoformat(),MAX_HISTORY)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return [],None
        return [],"INVALID_VERIFIED_EVIDENCE"
    except sqlite3.Error:
        return [],"INVALID_VERIFIED_EVIDENCE"

    cases=[]
    try:
        for (event,ticker,forecast_at,target_at,index_ms,index_receipt,
             frozen_raw,frozen_sha,settled_ticker,result,observed_f,observed_ms,
             checked_at,index_raw,index_sha,market_raw,market_sha) in data:
            forecast_time=_as_utc(forecast_at)
            target=_as_utc(target_at)
            checked=_as_utc(checked_at)
            index_receipt_time=_as_utc(index_receipt)
            if not (ticker==settled_ticker and
                    forecast_time<target<checked<=asof and
                    index_receipt_time<=forecast_time and
                    isinstance(index_ms,int) and
                    index_ms<=int(forecast_time.timestamp()*1000)):
                raise ValueError("IMPROPER_HOURLY_FORECAST_TIMING")
            if any(_sha(raw)!=digest for raw,digest in (
                    (frozen_raw,frozen_sha),(index_raw,index_sha),
                    (market_raw,market_sha))):
                raise ValueError("TAMPERED_HOURLY_EVIDENCE")
            frozen=json.loads(frozen_raw)
            if (frozen.get("city")!=city or frozen.get("source")!="KALSHI_WEATHER_INDEX" or
                    frozen.get("event_ticker")!=event or
                    frozen.get("target_utc")!=target_at or
                    frozen.get("selected_market",{}).get("ticker")!=ticker):
                raise ValueError("INVALID_HOURLY_SNAPSHOT")
            checked_index=parse_index(json.loads(index_raw),city,checked,
                                      max_age_seconds=8*24*3600)
            lower=int((target-timedelta(minutes=60)).timestamp()*1000)
            upper=int(target.timestamp()*1000)
            found=[r for r in checked_index if lower<=r["event_ms"]<=upper]
            if not found:
                raise ValueError("HOURLY_SETTLEMENT_MISSING_INDEX")
            canonical=max(found,key=lambda r:r["event_ms"])
            matching=next((m for m in parse_markets(
                json.loads(market_raw),city,event,settled=True)
                if m["ticker"]==ticker),None)
            if (matching is None or result not in ("yes","no") or
                    matching.get("result")!=result or
                    canonical["event_ms"]!=observed_ms or
                    abs(canonical["temp_f"]-float(observed_f))>1e-6 or
                    (canonical["temp_f"]>=matching["threshold_f"])!=(result=="yes")):
                raise ValueError("HOURLY_SETTLEMENT_MISMATCH")
            market=frozen["selected_market"]
            if abs(float(market["threshold_f"])-matching["threshold_f"])>1e-6:
                raise ValueError("HOURLY_MARKET_THRESHOLD_CHANGED")
            raw_mean=float(frozen.get("raw_predicted_index_f",
                                     frozen["predicted_index_f"]))
            raw_sigma=float(frozen.get("raw_sigma_f",frozen["sigma_f"]))
            current=float(frozen["current_index_f"])
            if not(all(math.isfinite(v) for v in (raw_mean,raw_sigma,current))
                    and raw_sigma>0):
                raise ValueError("INVALID_FROZEN_HOURLY_ESTIMATE")
            cases.append({
                "target_utc":target_at,
                "event_ticker":event,
                "market_ticker":ticker,
                "baseline_mean_f":raw_mean,
                "baseline_sigma_f":raw_sigma,
                "current_index_f":current,
                "threshold_f":matching["threshold_f"],
                "observed_index_f":canonical["temp_f"],
                "yes":int(result=="yes"),
            })
    except (ValueError,TypeError,KeyError,OverflowError,json.JSONDecodeError,
            AttributeError):
        return [], "INVALID_VERIFIED_EVIDENCE"
    return list(reversed(cases)),None


def learning_decision(conn: sqlite3.Connection, city: str,
                      asof: datetime) -> dict:
    cases,error=collect_verified_cases(conn,city,asof)
    if error:
        return {"enabled":False,"status":error,"verified_hours":0,
                "training_hours":0,"validation_hours":0,
                "validation":None}
    return choose_correction(cases)
