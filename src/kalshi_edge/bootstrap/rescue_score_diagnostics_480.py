"""Offline score diagnostics for skipped eight-minute forecasts."""
from __future__ import annotations
import math
from typing import Any, Sequence
from .confidence_reliability_480 import wilson_interval
from .reliability_480_core import BASELINE_GATE, gate_metrics

SCORE_EDGES=(0.,.5,.6,.7,.75,.8,.825,.85,.875,.9,.925,.95,1.)
TOP_K=(20,40,100,250)

def _percentile(values:list[float],q:float)->float|None:
    if not values:
        return None
    pos=q*(len(values)-1)
    i=int(pos)
    w=pos-i
    return values[i]*(1-w)+values[min(i+1,len(values)-1)]*w

def _detail(values:list[tuple[int,float,int]])->dict[str,Any]:
    n=len(values)
    correct=sum(hit for _i,_p,hit in values)
    lower,upper=wilson_interval(correct,n)
    return {
        "markets":n,"correct":correct,"incorrect":n-correct,
        "observed_accuracy":correct/n if n else None,
        "mean_estimated_correctness":sum(p for _i,p,_hit in values)/n if n else None,
        "wilson_95_lower":lower,"wilson_95_upper":upper,
        "small_sample_under_30":n<30,
    }
