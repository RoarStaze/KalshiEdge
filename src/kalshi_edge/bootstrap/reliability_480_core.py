"""Fixed causal gate features and scoring for the 480s research challenger."""
from __future__ import annotations
import math
from typing import Any, Sequence
from .specialist_480 import _eligible_features
from .types import FeatureRow

BASELINE_GATE = .69
RELIABILITY_GATE = .85
DIRECTION_C = .15
CORRECTNESS_C = .10
FOLDS = 4
MIN_OOF = 60
META_FEATURE_NAMES = (
    "direction_model_confidence", "kalshi_market_confidence",
    "model_market_direction_agreement", "absolute_normalized_strike_distance",
    "log_realized_vol_60s", "log_realized_vol_300s",
    "absolute_btc_return_60s", "kalshi_spread",
    "kalshi_quote_staleness_seconds",
)

def gate_metrics(labels: Sequence[int], p_yes: Sequence[float],
                 qualified: Sequence[bool]) -> dict[str,Any]:
    if len(labels)!=len(p_yes) or len(p_yes)!=len(qualified):
        raise ValueError("paired gate inputs must have matching lengths")
    for label,p,gate in zip(labels,p_yes,qualified):
        if (type(label) is not int or label not in (0,1)
            or type(p) not in (int,float) or not math.isfinite(p)
            or not 0<=p<=1 or type(gate) is not bool):
            raise ValueError("invalid paired gate evidence")
    count=sum(qualified)
    wins=sum(int((p>=.5)==bool(y)) for y,p,g in zip(labels,p_yes,qualified) if g)
    n=len(labels)
    return {
        "markets":n, "qualified":count,"correct":wins,
        "incorrect":count-wins,"skipped":n-count,
        "coverage":count/n if n else None,
        "accuracy":wins/count if count else None,
        "correct_per_market":wins/n if n else None,
        "note":"Directional accuracy only, not orders or net profit.",
    }

def reliability_features(row: FeatureRow, p_yes: float) -> list[float]:
    if (type(p_yes) not in (int,float) or not math.isfinite(p_yes)
        or not 0<=p_yes<=1 or _eligible_features(row) is not None):
        raise ValueError("noncausal or invalid correctness feature input")
    f=row.features
    mid=float(f["kalshi_mid"])
    x=[
        2*abs(p_yes-.5),
        2*abs(mid-.5),
        float((p_yes>=.5)==(mid>=.5)),
        abs(float(f["btc_normalized_distance"])),
        math.log(float(f["btc_realized_vol_60s"])),
        math.log(float(f["btc_realized_vol_300s"])),
        abs(float(f["btc_return_60s"])),
        float(f["kalshi_spread"]),
        float(f["kalshi_quote_staleness_seconds"]),
    ]
    if not all(math.isfinite(v) for v in x):
        raise ValueError("nonfinite correctness feature")
    return x

def classifier(c: float):
    from sklearn.pipeline import make_pipeline
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=c,solver="lbfgs",max_iter=1000,random_state=0),
    )
