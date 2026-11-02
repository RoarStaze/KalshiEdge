"""Causal 480-second out-of-fold correctness gate, research only.

Fits direction on historical development markets, then a separate
correctness classifier on expanding-window out-of-fold predictions.
Evaluates fixed rules on later calibration. No weights, orders or threshold
updates are written. This is not the deployed V5 model.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any,Sequence
from .specialist_480 import (
    FEATURES,MIN_TRAIN_MARKETS,MIN_VALIDATION_MARKETS,NS,
    select_eight_minute_rows,
)
from .types import FeatureRow
from .reliability_480_core import (
    BASELINE_GATE,RELIABILITY_GATE,DIRECTION_C,CORRECTNESS_C,FOLDS,MIN_OOF,
    META_FEATURE_NAMES,classifier,gate_metrics,reliability_features,
)

def _matrix(rows: Sequence[FeatureRow]) -> list[list[float]]:
    return [[r.features[n] for n in FEATURES] for r in rows]

def _group(r: FeatureRow) -> str:
    return r.split_group_id or r.market_ticker

def evaluate_reliability_gate(
    development_rows: Sequence[FeatureRow],
    calibration_rows: Sequence[FeatureRow],
) -> dict[str,Any]:
    train,te=select_eight_minute_rows(development_rows)
    valid,ve=select_eight_minute_rows(calibration_rows)
    out:dict[str,Any]={
        "report":"KXBTC15M_CAUSAL_480S_OOF_CORRECTNESS_GATE_V1",
        "status":"INSUFFICIENT_DIRECTION_TRAINING",
        "direction_fit_markets":len(train),
        "evaluation_markets":len(valid),
        "development_exclusions":te,
        "evaluation_exclusions":ve,
        "training_last_ns":train[-1].checkpoint_ts_ns if train else None,
        "evaluation_first_ns":valid[0].checkpoint_ts_ns if valid else None,
        "oof_folds":[],
        "oof_correctness_training_markets":0,
        "reliability_targets_correct":0,
        "reliability_targets_incorrect":0,
        "direction_features":list(FEATURES),
        "reliability_features":list(META_FEATURE_NAMES),
        "fixed_original_confidence":BASELINE_GATE,
        "fixed_correctness_probability":RELIABILITY_GATE,
        "baseline_69":None,"reliability_85":None,"rescue_union":None,
        "joint_objective_retrospective":False,
        "safety":{
            "model_saved":False,"ready_to_promote":False,
            "live_trading_changed":False,
            "independent_holdout":False,"reserved_lockbox_opened":False,
        },
        "note":(
            "Previously inspected pre-lockbox calibration is research, "
            "NOT independent evaluation. Six-feature logistic baseline is "
            "not deployed V5; no training weights saved or promoted. "
            "One fixed 0.85 correctness gate and one fixed 0.69 "
            "direction-confidence gate. Never infer after-fee profit."
        ),
    }
    tr_groups={_group(r) for r in train}
    va_groups={_group(r) for r in valid}
    if train and valid and (
        train[-1].checkpoint_ts_ns+900*NS>valid[0].checkpoint_ts_ns
        or tr_groups&va_groups
        or {r.market_ticker for r in train}&{r.market_ticker for r in valid}
    ):
        out["status"]="LEAKAGE_OR_OVERLAPPING_PERIODS"
        return out
    if len(train)<MIN_TRAIN_MARKETS:
        return out
    if len(valid)<MIN_VALIDATION_MARKETS:
        out["status"]="INSUFFICIENT_EVALUATION"
        return out
    if (len(tr_groups)!=len(train) or len(va_groups)!=len(valid)):
        out["status"]="AMBIGUOUS_MARKET_GROUPS"
        return out
    if len({r.label_yes for r in train})<2:
        out["status"]="SINGLE_CLASS_DIRECTION_TARGET"
        return out
    initial=max(MIN_TRAIN_MARKETS,len(train)//2)
    remaining=len(train)-initial
    if remaining<MIN_OOF:
        out["status"]="INSUFFICIENT_OOF_TRAINING"
        return out
    boundaries=[initial+remaining*i//FOLDS for i in range(FOLDS+1)]
    oof_x:list[list[float]]=[]
    oof_y:list[int]=[]
    for left,right in zip(boundaries[:-1],boundaries[1:]):
        prior,block=train[:left],train[left:right]
        if not block:
            continue
        if prior[-1].checkpoint_ts_ns+900*NS>block[0].checkpoint_ts_ns:
            out["status"]="LEAKAGE_OR_OVERLAPPING_PERIODS"
            return out
        if len({r.label_yes for r in prior})<2:
            out["status"]="SINGLE_CLASS_OOF_DIRECTION_TARGET"
            return out
        model=classifier(DIRECTION_C)
        model.fit(_matrix(prior),[r.label_yes for r in prior])
        p_block=model.predict_proba(_matrix(block))[:,1]
        for row,p in zip(block,p_block):
            p=float(p)
            oof_x.append(reliability_features(row,p))
            oof_y.append(int((p>=.5)==bool(row.label_yes)))
        out["oof_folds"].append({
            "training_markets":len(prior),"oof_markets":len(block),
            "training_max_ns":prior[-1].checkpoint_ts_ns,
            "evaluation_min_ns":block[0].checkpoint_ts_ns,
        })
    out["oof_correctness_training_markets"]=len(oof_y)
    out["reliability_targets_correct"]=sum(oof_y)
    out["reliability_targets_incorrect"]=len(oof_y)-sum(oof_y)
    if len(oof_y)<MIN_OOF:
        out["status"]="INSUFFICIENT_OOF_TRAINING"
        return out
    if len(set(oof_y))!=2:
        out["status"]="SINGLE_CLASS_RELIABILITY_TARGET"
        return out

    reliability=classifier(CORRECTNESS_C)
    reliability.fit(oof_x,oof_y)
    direction=classifier(DIRECTION_C)
    direction.fit(_matrix(train),[r.label_yes for r in train])
    pred=[float(p) for p in direction.predict_proba(_matrix(valid))[:,1]]
    x_valid=[reliability_features(r,p) for r,p in zip(valid,pred)]
    reliability_p=[float(p) for p in reliability.predict_proba(x_valid)[:,1]]
    original=[2*abs(p-.5)>=BASELINE_GATE for p in pred]
    qualified=[p>=RELIABILITY_GATE for p in reliability_p]
    union=[a or b for a,b in zip(original,qualified)]
    labels=[r.label_yes for r in valid]
    b=gate_metrics(labels,pred,original)
    candidate=gate_metrics(labels,pred,qualified)
    rescue=gate_metrics(labels,pred,union)
    out.update(
        status="RESEARCH_CALIBRATION_ONLY",
        baseline_69=b,
        reliability_85=candidate,
        rescue_union=rescue,
        meta_rescues_beyond_original=sum(int(y and not x)
                                         for x,y in zip(original,qualified)),
        joint_objective_retrospective=bool(
            candidate["coverage"]>b["coverage"]
            and candidate["accuracy"] is not None
            and b["accuracy"] is not None
            and candidate["accuracy"]>b["accuracy"]
            and candidate["correct_per_market"]>b["correct_per_market"]
        ),
        original_vs_reliability_accuracy_delta=(
            candidate["accuracy"]-b["accuracy"]
            if candidate["accuracy"] is not None
            and b["accuracy"] is not None else None),
        original_vs_reliability_coverage_delta=candidate["coverage"]-b["coverage"],
    )
    return out

def main(argv:list[str]|None=None)->int:
    from .config import BootstrapSettings
    from . import training
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=BootstrapSettings().bootstrap_dir)
    args=parser.parse_args(argv)
    try:
        root=args.root.resolve()
        ctx=training._verified_context(root)
        rows=training._read_rows_before_lockbox(ctx)
        dev=set(ctx.plan.development_groups)
        cal=set(ctx.plan.calibration_groups)
        report=evaluate_reliability_gate(
            [r for r in rows if training._group(r) in dev],
            [r for r in rows if training._group(r) in cal],
        )
        report["verified_feature_dataset_sha256"]=ctx.input_hashes[
            "derived/features.parquet"]
        report["partition"]="VERIFIED_PRELOCKBOX_DEVELOPMENT_THEN_CALIBRATION"
    except (OSError,ValueError,training.TrainingError) as exc:
        report={"status":"DATASET_UNAVAILABLE_OR_UNVERIFIED",
                "error":str(exc),"model_saved":False,
                "ready_to_promote":False,"reserved_lockbox_opened":False}
        print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
        return 2
    print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
