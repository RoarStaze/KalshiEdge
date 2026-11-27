"""WeatherEdge V13 experimental robust trend + prior-day-analogue candidate.

Fit only on earlier canonical, locally received hourly Index examples. No
market outcome, event settlement, or retrospective forecast may be a feature.
"""
from __future__ import annotations

import math
import statistics

MIN_TRAIN=72
PRIOR=(.35,.40,0.,0.)
RIDGE=(4.,4.,5.,12.)
NAMES=("fast_gain","slow_gain","diurnal_gain","bias_f_per_hour","sigma_scale")
SCALES=(.65,.80,1.,1.20,1.40,1.65,1.90)


def _number(value: object, lo: float, hi: float) -> float:
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError("INVALID_HYBRID_NUMBER")
    v=float(value)
    if not math.isfinite(v) or not lo<=v<=hi:
        raise ValueError("INVALID_HYBRID_NUMBER")
    return v


def _features(row: dict, *, training: bool) -> tuple[tuple[float,...],float,float]:
    """No future label is read during live prediction."""
    h=_number(row["hours_ahead"],.05,2.)
    fast=_number(row["fast_slope"],-12.,12.)
    slow=_number(row["slow_slope"],-12.,12.)
    analogue=_number(row["diurnal_delta_f"],-6.,6.)
    current=_number(row["current_index_f"],-129.,159.)
    sigma=_number(row["baseline_sigma_f"],.05,20.)
    y=(_number(row["observed_index_f"],-129.,159.)-current
       if training else 0.)
    return (fast*h,slow*h,analogue,h),y,sigma


def _solve(matrix: list[list[float]],vector: list[float]) -> list[float]:
    n=len(vector)
    aug=[list(matrix[i])+[vector[i]] for i in range(n)]
    for i in range(n):
        p=max(range(i,n),key=lambda j:abs(aug[j][i]))
        if abs(aug[p][i])<1e-9:
            raise ValueError("SINGULAR_HOURLY_HYBRID")
        aug[i],aug[p]=aug[p],aug[i]
        div=aug[i][i]
        for k in range(i,n+1):
            aug[i][k]/=div
        for j in range(n):
            if j==i:
                continue
            scale=aug[j][i]
            for k in range(i,n+1):
                aug[j][k]-=scale*aug[i][k]
    return [aug[i][n] for i in range(n)]


def _clip(b: list[float]) -> list[float]:
    fast=max(-.25,min(1.30,b[0]))
    slow=max(-.25,min(1.30,b[1]))
    diurnal=max(-.40,min(1.20,b[2]))
    bias=max(-2.,min(2.,b[3]))
    if max(0.,fast)+max(0.,slow)>1.50:
        factor=1.50/(max(0.,fast)+max(0.,slow))
        fast=fast*factor if fast>0 else fast
        slow=slow*factor if slow>0 else slow
    return [fast,slow,diurnal,bias]


def validate_hybrid(params: object) -> bool:
    if not isinstance(params,dict):
        return False
    try:
        b=[_number(params[k],-10.,10.) for k in NAMES]
        return (-.25<=b[0]<=1.30 and -.25<=b[1]<=1.30 and
                max(0.,b[0])+max(0.,b[1])<=1.500001 and
                -.40<=b[2]<=1.20 and -2.<=b[3]<=2. and
                .65<=b[4]<=1.90)
    except (KeyError,TypeError,ValueError):
        return False


def predict_hybrid(row: dict,params: dict) -> tuple[float,float]:
    if not validate_hybrid(params):
        raise ValueError("INVALID_HYBRID_PARAMETERS")
    features,_,base_sigma=_features(row,training=False)
    current=_number(row["current_index_f"],-129.,159.)
    beta=[params[k] for k in NAMES[:4]]
    shift=sum(beta[j]*features[j] for j in range(4))
    mean=current+max(-8.,min(8.,shift))
    if not -130.<mean<160.:
        raise ValueError("OUT_OF_RANGE_HYBRID_FORECAST")
    return mean,max(.05,base_sigma*params["sigma_scale"])


def _crps(mean: float,sigma: float,actual: float) -> float:
    z=(actual-mean)/sigma
    return sigma*(z*(2*(.5*math.erfc(-z/math.sqrt(2.)))-1.)+
                  2*math.exp(-.5*z*z)/math.sqrt(2*math.pi)-
                  1./math.sqrt(math.pi))


def fit_hybrid(rows: list[dict]) -> dict:
    # The first 1–2 days of an eight-day receipt-safe history may not
    # contain a complete prior-day analogue. Exclude only genuinely absent
    # features; a present malformed/nonfinite value must still fail closed.
    eligible=[r for r in rows if r.get("diurnal_delta_f") is not None]
    if len(eligible)<MIN_TRAIN:
        raise ValueError("INSUFFICIENT_HYBRID_TRAINING")
    samples=[_features(row,training=True) for row in eligible]
    if sum(abs(x[2])>=.35 for x,_,_ in samples)<24:
        raise ValueError("INSUFFICIENT_DIURNAL_DIVERSITY")
    beta=list(PRIOR)
    for _ in range(6):
        residual=[y-sum(beta[j]*x[j] for j in range(4))
                  for x,y,_ in samples]
        center=statistics.median(residual)
        mad=statistics.median(abs(e-center) for e in residual)
        threshold=max(.18,1.75*1.4826*mad)
        gram=[[RIDGE[i] if i==j else 0. for j in range(4)] for i in range(4)]
        rhs=[RIDGE[i]*PRIOR[i] for i in range(4)]
        for (x,y,_),err in zip(samples,residual):
            weight=min(1.,threshold/max(1e-8,abs(err)))
            for i in range(4):
                rhs[i]+=weight*x[i]*y
                for j in range(4):
                    gram[i][j]+=weight*x[i]*x[j]
        beta=_clip(_solve(gram,rhs))
    # Sigma chosen strictly from training: train-CRPS on a small fixed grid.
    best=min(SCALES,key=lambda scale:sum(
        _crps(row["current_index_f"]+max(-8.,min(8.,sum(beta[i]*x[i]
               for i in range(4)))),max(.05,sigma*scale),
               row["observed_index_f"]) for row,(x,_,sigma) in zip(eligible,samples)))
    result={name:round(beta[i],6) for i,name in enumerate(NAMES[:4])}
    result["sigma_scale"]=best
    if not validate_hybrid(result):
        raise ValueError("INVALID_FITTED_HYBRID")
    return result
