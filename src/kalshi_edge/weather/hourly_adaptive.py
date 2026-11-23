"""Robust, bounded per-lead hourly Kalshi Index trend regression.

This learns a *temperature forecast* using only past, locally received Kalshi
Index readings. It never substitutes weather-station or synthetic settlement
labels, calls external endpoints or participates in order execution.
"""
from __future__ import annotations

import math
import statistics


_PRIOR=(.35,.40,0.)
_RIDGE=(3.,3.,12.)
_KEYS=("fast_gain","slow_gain","bias_per_hour","sigma_scale")


def _solve3(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Small regularized normal equation with pivoting; no numpy dependency."""
    a=[list(matrix[i])+[vector[i]] for i in range(3)]
    for col in range(3):
        pivot=max(range(col,3),key=lambda idx:abs(a[idx][col]))
        if abs(a[pivot][col])<1e-9:
            raise ValueError("SINGULAR_HOURLY_TREND_FIT")
        a[col],a[pivot]=a[pivot],a[col]
        d=a[col][col]
        for j in range(col,4):
            a[col][j]/=d
        for row in range(3):
            if row==col:
                continue
            factor=a[row][col]
            for j in range(col,4):
                a[row][j]-=factor*a[col][j]
    return [a[i][3] for i in range(3)]


def _clean(rows: list[dict]) -> list[tuple[list[float],float,float]]:
    cleaned=[]
    for row in rows:
        try:
            fast=float(row["fast_slope"])
            slow=float(row["slow_slope"])
            h=float(row["hours_ahead"])
            actual=float(row["observed_index_f"])
            current=float(row["current_index_f"])
            sigma=float(row["baseline_sigma_f"])
            if not all(math.isfinite(v) for v in (fast,slow,h,actual,current,sigma)):
                raise ValueError("NONFINITE_HOURLY_TRAINING_CASE")
            if not (.05<=h<=2.0 and 0.<sigma<=20. and
                    -130<actual<160 and -130<current<160 and
                    abs(fast)<=12 and abs(slow)<=12):
                raise ValueError("OUT_OF_RANGE_HOURLY_TRAINING_CASE")
            cleaned.append(([fast,slow,1.],(actual-current)/h,sigma))
        except (KeyError,TypeError,OverflowError) as exc:
            raise ValueError("INVALID_HOURLY_TRAINING_CASE") from exc
    if len(cleaned)<36:
        raise ValueError("INSUFFICIENT_ADAPTIVE_TRAINING_CASES")
    return cleaned


def _bound(beta: list[float]) -> list[float]:
    fast=max(-.30,min(1.25,beta[0]))
    slow=max(-.30,min(1.25,beta[1]))
    positive=max(0.,fast)+max(0.,slow)
    if positive>1.5:
        fast*=1.5/positive if fast>0 else 1.
        slow*=1.5/positive if slow>0 else 1.
    drift=max(-2.,min(2.,beta[2]))
    return [fast,slow,drift]


def fit_adaptive(rows: list[dict]) -> dict:
    """Train-only Huber IRLS ridge against current index's future delta.

    L2 shrinkage avoids unstable fits when fast and slow slopes correlate;
    Huber weights prevent a single bad (but source-valid) temperature hour
    from hijacking the live forecast.
    """
    cases=_clean(rows)
    beta=list(_PRIOR)
    for _ in range(7):
        residual=[y-sum(x[j]*beta[j] for j in range(3))
                  for x,y,_ in cases]
        center=statistics.median(residual)
        mad=statistics.median(abs(value-center) for value in residual)
        huber=max(.20,1.75*1.4826*mad)
        gram=[[(_RIDGE[i] if i==j else 0.) for j in range(3)]
              for i in range(3)]
        rhs=[_RIDGE[i]*_PRIOR[i] for i in range(3)]
        for (x,y,_),err in zip(cases,residual):
            weight=min(1.,huber/max(abs(err),1e-8))
            for i in range(3):
                rhs[i]+=weight*x[i]*y
                for j in range(3):
                    gram[i][j]+=weight*x[i]*x[j]
        beta=_bound(_solve3(gram,rhs))
    # Train-only uncertainty, robustly estimated then shrunk toward the
    # incumbent. Holdout outcomes must never tune the uncertainty factor.
    # Residuals are degrees per hour; multiply by their original lead
    # before normalizing by the incumbent baseline's temperature sigma.
    residuals=[]
    for row,(x,y,sigma) in zip(rows,cases):
        h=float(row["hours_ahead"])
        residuals.append(abs(y-sum(x[j]*beta[j] for j in range(3)))*h/sigma)
    mad_sigma=1.4826*statistics.median(residuals)
    # 35% regularization toward incumbent sigma is deliberate: sparse
    # markets should not claim 90% certainty from nearly perfect replay.
    scale=max(.75,min(1.65,.35+.65*mad_sigma))
    return {"fast_gain":round(beta[0],6),
            "slow_gain":round(beta[1],6),
            "bias_per_hour":round(beta[2],6),
            "sigma_scale":round(scale,6)}


def validate_adaptive(parameters: object) -> bool:
    if not isinstance(parameters,dict):
        return False
    try:
        values=[parameters[key] for key in _KEYS]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or
               not math.isfinite(v) for v in values):
            return False
        fast,slow,bias,scale=values
        return (-.30<=fast<=1.25 and -.30<=slow<=1.25 and
                max(0.,fast)+max(0.,slow)<=1.500001 and
                -2.<=bias<=2. and .75<=scale<=1.65)
    except (KeyError,TypeError):
        return False


def predict_adaptive(current_f: float, sigma_f: float,
                     fast_slope: float, slow_slope: float,
                     hours_ahead: float, parameters: dict
                     ) -> tuple[float,float]:
    """Fail closed on invalid fit or live inputs; returns Fahrenheit & sigma."""
    if not validate_adaptive(parameters):
        return current_f,sigma_f
    if (not all(isinstance(v,(int,float)) and not isinstance(v,bool) and
                math.isfinite(v) for v in
                (current_f,sigma_f,fast_slope,slow_slope,hours_ahead))
        or not (-130<current_f<160 and 0<sigma_f<=20 and
                0<hours_ahead<=2.0 and
                abs(fast_slope)<=12 and abs(slow_slope)<=12)):
        return current_f,sigma_f
    drift=(parameters["fast_gain"]*fast_slope+
           parameters["slow_gain"]*slow_slope+
           parameters["bias_per_hour"])
    forecast=current_f+hours_ahead*drift
    if not -130<forecast<160:
        return current_f,sigma_f
    return forecast,max(.05,sigma_f*parameters["sigma_scale"])
