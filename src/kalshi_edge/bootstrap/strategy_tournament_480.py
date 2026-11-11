"""Offline 480-second BTC strategy tournament; strictly research-only.

Translate *ideas* from public TradingView trend / pullback / reversion
strategies into independently authored, fixed checkpoint-time scoring rules.
Not a Pine interpreter, not a TradingKit backtest, not a fill simulator.
Requires authentic precommitted Kalshi 480s feature snapshots and separately
verified official settlement outcomes. No model promotion or trading side effects.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .final_forecast import FINAL_CHECKPOINT_SECONDS
from .final_history import _close_ns, _forecasts, _load_saved_settlement
from .forward_feature_evidence_480 import load_forward_snapshot
from .metrics import probability_metrics

NS = 1_000_000_000
GATE = .69
# Four fixed hypotheses, defined before inspecting validation/holdout labels.
# They are *conceptual* translations, not copies of any third-party Pine code.
STRATEGIES: dict[str, tuple[float,float,float]] = {
    "strike_distance": (1.9, 0.00, 0.00),
    "trend_confirmation": (1.9, 0.65, 0.30),
    "pullback": (1.9, -0.35, 0.55),
    "mean_reversion": (1.9, -0.55, -0.10),
}
MIN_MARKETS = 240
MIN_DAYS = 5


@dataclass(frozen=True)
class Case:
    ticker: str
    checkpoint_ts_ns: int
    settlement_fetched_ts_ns: int
    label_yes: int
    incumbent_p_yes: float
    coinflip_high: bool
    features: Mapping[str, float]


def _valid(value: object) -> bool:
    return (type(value) in (float, int) and math.isfinite(value))


def _clamp(value: float, limit: float) -> float:
    return min(limit, max(-limit, value))


def candidate_probability(name: str, f: Mapping[str,float]) -> float | None:
    """A probability calculated only from pre-checkpoint completed observations.

    Unavailable, nonfinite or degenerate volatility/return sources fail
    closed; no replacement from a future candle or settlement is permitted.
    """
    if name not in STRATEGIES:
        raise ValueError(f"Unknown frozen candidate: {name}")
    keys = (
        "btc_normalized_distance", "btc_return_60s", "btc_return_300s",
        "btc_realized_vol_60s",
    )
    if any(key not in f or not _valid(f[key]) for key in keys):
        return None
    if (f["btc_realized_vol_60s"] <= 0 or
            f.get("btc_return_60s_available") != 1.0 or
            f.get("btc_return_300s_available") != 1.0):
        return None
    dist = _clamp(float(f["btc_normalized_distance"]), 5)
    vol = float(f["btc_realized_vol_60s"])
    r60 = _clamp(float(f["btc_return_60s"])/(vol*math.sqrt(60)), 4)
    r300 = _clamp(float(f["btc_return_300s"])/(vol*math.sqrt(300)), 4)
    d,m,s=STRATEGIES[name]
    logit=_clamp(d*dist+m*r60+s*r300, 12)
    return 1.0/(1.0+math.exp(-logit))


def read_verified_cases(root: Path) -> list[Case]:
    """No reconstructed snapshots and no dataset labels; official results only."""
    root=Path(root).resolve()
    cases:list[Case]=[]
    for final in _forecasts(root):
        if final.checkpoint_seconds!=FINAL_CHECKPOINT_SECONDS or not _valid(final.p_yes):
            continue
        if not 0<=final.p_yes<=1:
            continue
        try:
            snapshot=load_forward_snapshot(root,final)
            settlement=_load_saved_settlement(root,final)
            if (settlement is None or snapshot.status!="FEATURES_OK"
                    or settlement.fetched_ts_ns<=_close_ns(final)):
                continue
            if not all(candidate_probability(name,snapshot.features) is not None
                       for name in STRATEGIES):
                continue
            # The source must be a fresh, genuinely pre-checkpoint observation.
            binance_ns=snapshot.source_max_ts_ns.get("binance")
            if (type(binance_ns) is not int
                    or not 0<=snapshot.prediction_ts_ns-binance_ns<=5*NS):
                continue
            cases.append(Case(
                ticker=final.market_ticker,
                checkpoint_ts_ns=final.prediction_ts_ns,
                settlement_fetched_ts_ns=settlement.fetched_ts_ns,
                label_yes=int(settlement.actual_side=="ABOVE"),
                incumbent_p_yes=float(final.p_yes),
                coinflip_high=(final.coin_flip_assessment=="HIGH"),
                features=dict(snapshot.features),
            ))
        except (ValueError,OSError,KeyError,OverflowError):
            # Never train on a corrupted or incompletely sourced record.
            continue
    cases.sort(key=lambda c:(c.checkpoint_ts_ns,c.ticker))
    return cases


def _gate_score(cases: Sequence[Case], probs: Sequence[float]) -> dict:
    labels=[case.label_yes for case in cases]
    pstats=probability_metrics(labels,probs).model_dump(mode="json")
    qualified=sum(
        not case.coinflip_high and 2*abs(p-.5)>=GATE
        for case,p in zip(cases,probs)
    )
    correct=sum(
        not case.coinflip_high and 2*abs(p-.5)>=GATE
        and (p>=.5)==bool(case.label_yes)
        for case,p in zip(cases,probs)
    )
    return {
        "total":len(cases),"qualified":qualified,"correct":correct,
        "coverage":qualified/len(cases) if cases else None,
        "qualified_accuracy":correct/qualified if qualified else None,
        "correct_per_all_markets":correct/len(cases) if cases else None,
        "brier":pstats["brier"],"log_loss":pstats["log_loss"],
        "ece":pstats["ece"],
    }


def _score(name: str, cases: Sequence[Case]) -> dict:
    if not cases:
        raise ValueError("cannot score empty market cohort")
    if name=="incumbent":
        p=[case.incumbent_p_yes for case in cases]
    else:
        p=[candidate_probability(name,case.features) for case in cases]
        if any(x is None for x in p):
            raise ValueError("cannot score candidate on unavailable common features")
    return _gate_score(cases,p)


def _beats(candidate: dict, incumbent: dict) -> bool:
    """Necessary screens only. They do not establish trade profitability."""
    c_acc=candidate["qualified_accuracy"]
    i_acc=incumbent["qualified_accuracy"]
    return (
        c_acc is not None and i_acc is not None
        and candidate["qualified"]>0 and incumbent["qualified"]>0
        and candidate["brier"] < incumbent["brier"]
        and candidate["log_loss"] < incumbent["log_loss"]
        and candidate["coverage"] > incumbent["coverage"]
        and c_acc > i_acc
    )


def evaluate_tournament(
    cases: Sequence[Case], *, min_total: int = MIN_MARKETS, min_days: int = MIN_DAYS,
) -> dict:
    """Train shortlist -> validation winner -> ONE held-out, unchanged final test.

    The untrusted / previously unseen test set is NEVER used to shortlist or
    select the candidate. Settlement fetch dates are respected across each
    partition boundary. This report never changes live forecast or AutoTrade.
    """
    if min_total<3 or min_days<1:
        raise ValueError("invalid evidence minimum")
    seen=set()
    ordered=[]
    for case in sorted(cases,key=lambda c:(c.checkpoint_ts_ns,c.ticker)):
        if (case.ticker in seen or type(case.label_yes) is not int
                or case.label_yes not in (0,1)
                or not _valid(case.incumbent_p_yes)
                or not 0<=case.incumbent_p_yes<=1
                or type(case.settlement_fetched_ts_ns) is not int
                or case.settlement_fetched_ts_ns<=case.checkpoint_ts_ns
                or any(candidate_probability(name,case.features) is None
                       for name in STRATEGIES)):
            raise ValueError("invalid, missing-feature or duplicate verified market")
        seen.add(case.ticker)
        ordered.append(case)

    distinct_days=len({
        datetime.fromtimestamp(c.checkpoint_ts_ns/NS,tz=timezone.utc).date()
        for c in ordered
    })
    base={
        "report":"KALSHIEDGE_KXBTC15M_480S_STRATEGY_TOURNAMENT_V1",
        "status":"INSUFFICIENT_VERIFIED_MARKETS",
        "data_source":"OFFICIAL_SETTLEMENT_AND_IMMUTABLE_CHECKPOINT_SNAPSHOT",
        "research_only":True, "ready_to_promote":False,
        "live_forecasts_changed":False,"autotrade_changed":False,
        "verified_net_profit":False,"independent_utc_days":distinct_days,
        "counts":{"paired_eligible_markets":len(ordered)},
        "selection":{"candidate":None,"validation_scores":{}},
        "validation":{},"test":{},
        "promotion_review":{"automatic_promotion":False,"eligible":False},
        "limitations": (
            "Only 8-minute KXBTC15M directional evidence is evaluated. No "
            "TradingKit MCP backtest or real executed orders were accessible; "
            "no fill/slippage/fee-adjusted net profits are verified. "
            "No live or AutoTrade decisions are modified."
        ),
    }
    if len(ordered)<min_total or distinct_days<min_days:
        base["requirements"]={
            "minimum_markets":min_total,"minimum_utc_days":min_days,
        }
        return base
    count=len(ordered)
    train_end=int(.60*count)
    test_begin=int(.80*count)
    train=ordered[:train_end]
    val=ordered[train_end:test_begin]
    holdout=ordered[test_begin:]
    val_time=val[0].checkpoint_ts_ns
    test_time=holdout[0].checkpoint_ts_ns
    training_available=[c for c in train if c.settlement_fetched_ts_ns<val_time]
    validation_available=[c for c in val if c.settlement_fetched_ts_ns<test_time]
    base["counts"].update({
        "training_markets":len(train),"validation_markets":len(val),
        "test_markets":len(holdout),
        "training_available_by_validation_start":len(training_available),
        "validation_available_by_test_start":len(validation_available),
    })
    if (len(training_available)<max(20,int(.5*len(train)))
            or len(validation_available)<max(10,int(.5*len(val)))
            or len(holdout)<40):
        base["status"]="INSUFFICIENT_CHRONOLOGICAL_COHORT"
        return base
    all_names=("incumbent",*STRATEGIES)
    training_scores={name:_score(name,training_available) for name in all_names}
    # Train a fixed TWO-rule shortlist on the first 60%; earlier labels only.
    shortlist=sorted(
        STRATEGIES,
        key=lambda name:(
            training_scores[name]["brier"],
            training_scores[name]["log_loss"],name,
        ),
    )[:2]
    validation_scores={
        name:_score(name,validation_available)
        for name in ("incumbent",*shortlist)
    }
    base["validation"]=validation_scores
    # Select only with the 20% validation cohort; the holdout is untouchable.
    valid=[name for name in shortlist
           if _beats(validation_scores[name],validation_scores["incumbent"])]
    selected=(min(
        valid,
        key=lambda name:(
            validation_scores[name]["brier"],
            -validation_scores[name]["correct_per_all_markets"],
            name,
        ),
    ) if valid else None)
    base["selection"]={
        "candidate":selected,
        "shortlist":shortlist,
        "training_scores":training_scores,
        "validation_scores":validation_scores,
        "validated_improvement_over_incumbent":selected is not None,
    }
    base["test"]={"incumbent":_score("incumbent",holdout)}
    if selected is not None:
        base["test"][selected]=_score(selected,holdout)
    qualifies=(selected is not None and _beats(
        base["test"][selected],base["test"]["incumbent"]
    ))
    base["status"]=(
        "RESEARCH_HOLDOUT_IMPROVEMENT" if qualifies
        else "NO_VALIDATED_HOLDOUT_WINNER"
    )
    base["promotion_review"]={
        "automatic_promotion":False,
        "eligible":False,
        "heldout_probabilities_and_gate_improved":bool(qualifies),
        "further_prospective_evidence_required":True,
        "actual_executable_net_profit_missing":True,
    }
    return base


def main(argv:list[str]|None=None) -> int:
    from .config import BootstrapSettings
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=BootstrapSettings().bootstrap_dir)
    args=parser.parse_args(argv)
    result=evaluate_tournament(read_verified_cases(args.root))
    print(json.dumps(result,sort_keys=True,indent=2,allow_nan=False))
    return 0 if result["status"]!="INSUFFICIENT_VERIFIED_MARKETS" else 2


if __name__=="__main__":
    raise SystemExit(main())
