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


def describe_rescue_scores(
    labels:Sequence[int],direction_p_yes:Sequence[float],
    correctness_probabilities:Sequence[float],
)->dict[str,Any]:
    """Describe scores on already-used development selection only."""
    if len(labels)!=len(direction_p_yes) or len(labels)!=len(correctness_probabilities):
        raise ValueError("paired probabilities and labels have unequal lengths")
    gate_metrics(labels,direction_p_yes,[True]*len(labels))
    if any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1
           for p in correctness_probabilities):
        raise ValueError("invalid estimated correctness probability")

    skip_rows:list[tuple[int,float,int]]=[]
    originally_issued=0
    for i,(label,p,m) in enumerate(zip(labels,direction_p_yes,correctness_probabilities)):
        if 2*abs(p-.5)>=BASELINE_GATE:
            originally_issued+=1
            continue
        skip_rows.append((i,float(m),int((p>=.5)==bool(label))))

    sorted_rows=sorted(skip_rows,key=lambda item:(-item[1],item[0]))
    ordered=sorted(item[1] for item in skip_rows)
    histogram=[]
    for low,high in zip(SCORE_EDGES[:-1],SCORE_EDGES[1:]):
        matching=[item for item in skip_rows
                  if low<=item[1]<high or (high==1. and item[1]==1.)]
        histogram.append({"min_score":low,"max_score":high,**_detail(matching)})

    n=len(skip_rows)
    correct=sum(item[2] for item in skip_rows)
    if 0<correct<n:
        from sklearn.metrics import roc_auc_score
        auc=float(roc_auc_score([item[2] for item in skip_rows],
                                [item[1] for item in skip_rows]))
    else:
        auc=None

    return {
        "report":"KXBTC15M_OOF_480S_SKIPPED_SCORE_DIAGNOSTIC_V1",
        "source":"DEVELOPMENT_THRESHOLD_SELECTION_OOF_ONLY",
        "original_skips":n,
        "original_qualifying_markets_excluded":originally_issued,
        "skipped_direction_correct":correct,
        "skipped_direction_wrong":n-correct,
        "skipped_direction_accuracy":correct/n if n else None,
        "above_90_percent":sum(p>=.9 for _i,p,_hit in skip_rows),
        "score_range":{
            "min":ordered[0] if ordered else None,
            "p10":_percentile(ordered,.10),
            "p25":_percentile(ordered,.25),
            "median":_percentile(ordered,.5),
            "p75":_percentile(ordered,.75),
            "p90":_percentile(ordered,.9),
            "p95":_percentile(ordered,.95),
            "max":ordered[-1] if ordered else None,
        },
        "bins":histogram,
        "top_k":[{"requested_top_k":k,**_detail(sorted_rows[:k])} for k in TOP_K],
        "roc_auc_on_skipped_direction_correctness":auc,
        "brier_of_estimated_correctness_on_skips":(
            sum((p-hit)**2 for _i,p,hit in skip_rows)/n if n else None
        ),
        "ready_to_promote":False,
        "note":(
            "Fixed bins and top-K lists are retrospective diagnostics, "
            "not policy-optimization or independent validation. Only the "
            "already-inspected third chronological development OOF fold "
            "is analyzed. Internal confirmation and historical calibration "
            "are not accessed when threshold selection fails."
        ),
    }
