"""Fixed causal four-signal BTC feature challenger for KXBTC15M at 480 seconds.

Offline research only. Compares the same fixed logistic, six-feature
nonlinear, and enriched ten-feature nonlinear models on identical markets.
No model artifact, threshold change, exchange action or AutoTrade changes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .nonlinear_direction_480 import (
    _nonlinear_model, _result_row,
    MIN_DEVELOPMENT, MIN_SCREEN, MIN_CONFIRM, MIN_QUALIFIED,
    FIT_FRACTION, SCREEN_FRACTION,
)
from .reliability_480_core import BASELINE_GATE, DIRECTION_C, classifier
from .specialist_480 import (
    FEATURES, NS, MIN_VALIDATION_MARKETS,
    _eligible_features, select_eight_minute_rows,
)
from .types import FeatureRow

# Fixed before any scoring. All four are computed solely from Binance bars
# completed at or before the checkpoint in bootstrap/features.py.
EXTRA_FEATURES = (
    "btc_return_15s",
    "btc_taker_buy_imbalance_60s",
    "btc_market_high_excursion_bps",
    "btc_market_low_excursion_bps",
)
ENRICHED_FEATURES = FEATURES + EXTRA_FEATURES


def enriched_feature_reason(row: FeatureRow) -> str | None:
    """Validate actual at-checkpoint additional evidence, never impute."""
    if _eligible_features(row) is not None:
        return "invalid_base_features"
    f = row.features
    available = f.get("btc_return_15s_available")
    if (type(available) not in (int,float)
            or not math.isfinite(available) or available < 1):
        return "missing_15s_return"
    for name in EXTRA_FEATURES:
        x = f.get(name)
        if type(x) not in (int,float) or not math.isfinite(x):
            return "missing_or_nonfinite_enriched_feature"
    if not -1 <= f["btc_taker_buy_imbalance_60s"] <= 1:
        return "invalid_btc_order_flow"
    if f["btc_market_low_excursion_bps"] > f["btc_market_high_excursion_bps"]:
        return "invalid_btc_excursion"
    if f["btc_return_15s"] <= -1:
        return "invalid_btc_return"
    return None


def select_enriched_rows(
    rows: Sequence[FeatureRow],
) -> tuple[list[FeatureRow],dict[str,int]]:
    base, exclusions = select_eight_minute_rows(rows)
    counts = Counter(exclusions)
    good = []
    for row in base:
        reason = enriched_feature_reason(row)
        if reason is None:
            good.append(row)
        else:
            counts[reason] += 1
    return good, dict(counts)


def _matrix(rows: Sequence[FeatureRow], names: tuple[str,...]):
    return [[row.features[name] for name in names] for row in rows]


def _groups(rows: Sequence[FeatureRow]) -> set[str]:
    return {row.split_group_id or row.market_ticker for row in rows}


def _joint(candidate:dict[str,Any],other:dict[str,Any]) -> bool:
    c,b = candidate["gate_69"],other["gate_69"]
    return bool(
        c["qualified"] >= MIN_QUALIFIED
        and c["accuracy"] is not None
        and b["accuracy"] is not None
        and c["coverage"] > b["coverage"]
        and c["accuracy"] > b["accuracy"]
        and c["correct_per_market"] > b["correct_per_market"]
    )


def _compare(
    rows: Sequence[FeatureRow], models:dict[str,Any],
) -> dict[str,Any]:
    if not rows:
        raise ValueError("cannot evaluate an empty period")
    y = [r.label_yes for r in rows]
    x6 = _matrix(rows, FEATURES)
    x10 = _matrix(rows, ENRICHED_FEATURES)
    scores = {
        "logistic_6":_result_row(y,[
            float(x) for x in models["logistic_6"].predict_proba(x6)[:,1]
        ]),
        "nonlinear_6":_result_row(y,[
            float(x) for x in models["nonlinear_6"].predict_proba(x6)[:,1]
        ]),
        "nonlinear_enriched_10":_result_row(y,[
            float(x) for x in models["nonlinear_enriched_10"].predict_proba(x10)[:,1]
        ]),
    }
    challenger = scores["nonlinear_enriched_10"]
    return {
        "markets":len(rows),
        **scores,
        "joint_vs_logistic":_joint(challenger,scores["logistic_6"]),
        "joint_vs_nonlinear_6":_joint(challenger,scores["nonlinear_6"]),
        "joint_objective":(
            _joint(challenger,scores["logistic_6"])
            and _joint(challenger,scores["nonlinear_6"])
        ),
        "baseline_logistic_accuracy_delta":(
            challenger["gate_69"]["accuracy"]
            - scores["logistic_6"]["gate_69"]["accuracy"]
            if (challenger["gate_69"]["accuracy"] is not None
                and scores["logistic_6"]["gate_69"]["accuracy"] is not None)
            else None
        ),
        "baseline_logistic_coverage_delta":(
            challenger["gate_69"]["coverage"]-scores["logistic_6"]["gate_69"]["coverage"]
        ),
        "note":"Same markets, same 69% confidence policy; includes ALL incorrect forecasts.",
    }


def evaluate_enriched_direction(
    development_rows: Sequence[FeatureRow],
    calibration_rows: Sequence[FeatureRow],
) -> dict[str,Any]:
    """Fit three fixed models on one 480s cohort; require two joint passes."""
    development, dev_ex = select_enriched_rows(development_rows)
    historical, hist_ex = select_enriched_rows(calibration_rows)
    out:dict[str,Any] = {
        "report":"KXBTC15M_480S_FOUR_EXTRA_BTC_SIGNALS_V1",
        "status":"INSUFFICIENT_DEVELOPMENT",
        "eligible_development_markets":len(development),
        "eligible_historical_calibration_markets":len(historical),
        "development_exclusions":dev_ex,
        "historical_calibration_exclusions":hist_ex,
        "extra_features":list(EXTRA_FEATURES),
        "original_features":list(FEATURES),
        "feature_count_baselines":len(FEATURES),
        "feature_count_challenger":len(ENRICHED_FEATURES),
        "gate_confidence":BASELINE_GATE,
        "fit_markets":0,
        "screen_markets":0,
        "confirm_markets":0,
        "fit_max_ns":None,
        "screen_min_ns":None,
        "screen_max_ns":None,
        "confirm_min_ns":None,
        "confirm_max_ns":None,
        "calibration_min_ns":(
            historical[0].checkpoint_ts_ns if historical else None
        ),
        "screen":None,
        "confirm":None,
        "historical_calibration":None,
        "joint_objective_retrospective":False,
        "model_search_performed":False,
        "threshold_changed":False,
        "safety":{
            "model_saved":False,
            "ready_to_promote":False,
            "live_trading_changed":False,
            "reserved_lockbox_opened":False,
            "independent_validation_proven":False,
        },
        "note":(
            "Fixed four extra Binance-derived features known at the 480-second "
            "checkpoint. Three models fit on exactly the same early development "
            "markets. Strict 69% gate unchanged; no tuning. Screen and confirm "
            "are repeatedly inspected historical research, NOT independent. "
            "Both development blocks must beat BOTH logistic-six and "
            "HGBT-six on coverage, qualified accuracy and correct-per-market; "
            "otherwise do not evaluate previously inspected calibration. "
            "Neither research baseline is deployed V5. No models persisted, "
            "no live forecasts/trades/thresholds changed and no lockbox read."
        ),
    }

    if development and historical and (
        development[-1].checkpoint_ts_ns+900*NS>historical[0].checkpoint_ts_ns
        or {r.market_ticker for r in development}.intersection(
            r.market_ticker for r in historical
        )
        or _groups(development).intersection(_groups(historical))
    ):
        out["status"]="LEAKAGE_OR_OVERLAPPING_PERIODS"
        return out
    if len(development)<MIN_DEVELOPMENT:
        return out
    if len(historical)<MIN_VALIDATION_MARKETS:
        out["status"]="INSUFFICIENT_CALIBRATION"
        return out
    if (
        len(_groups(development))!=len(development)
        or len(_groups(historical))!=len(historical)
    ):
        out["status"]="AMBIGUOUS_MARKET_GROUPS"
        return out

    count=len(development)
    fit_end=int(count*FIT_FRACTION)
    screen_end=int(count*(FIT_FRACTION+SCREEN_FRACTION))
    fit=development[:fit_end]
    screen=development[fit_end:screen_end]
    confirm=development[screen_end:]
    out.update(
        fit_markets=len(fit),
        screen_markets=len(screen),
        confirm_markets=len(confirm),
        fit_max_ns=fit[-1].checkpoint_ts_ns if fit else None,
        screen_min_ns=screen[0].checkpoint_ts_ns if screen else None,
        screen_max_ns=screen[-1].checkpoint_ts_ns if screen else None,
        confirm_min_ns=confirm[0].checkpoint_ts_ns if confirm else None,
        confirm_max_ns=confirm[-1].checkpoint_ts_ns if confirm else None,
    )
    if len(fit)<MIN_DEVELOPMENT*FIT_FRACTION or len(screen)<MIN_SCREEN or len(confirm)<MIN_CONFIRM:
        out["status"]="INSUFFICIENT_DEVELOPMENT_BLOCKS"
        return out
    if (
        fit[-1].checkpoint_ts_ns+900*NS>screen[0].checkpoint_ts_ns
        or screen[-1].checkpoint_ts_ns+900*NS>confirm[0].checkpoint_ts_ns
        or confirm[-1].checkpoint_ts_ns+900*NS>historical[0].checkpoint_ts_ns
        or _groups(fit).intersection(_groups(screen))
        or _groups(fit).intersection(_groups(confirm))
        or _groups(screen).intersection(_groups(confirm))
    ):
        out["status"]="LEAKAGE_OR_OVERLAPPING_PERIODS"
        return out
    if {r.label_yes for r in fit}!={0,1}:
        out["status"]="SINGLE_CLASS_TRAIN"
        return out

    models={
        "logistic_6":classifier(DIRECTION_C),
        "nonlinear_6":_nonlinear_model(),
        "nonlinear_enriched_10":_nonlinear_model(),
    }
    y=[r.label_yes for r in fit]
    x6=_matrix(fit,FEATURES)
    x10=_matrix(fit,ENRICHED_FEATURES)
    models["logistic_6"].fit(x6,y)
    models["nonlinear_6"].fit(x6,y)
    models["nonlinear_enriched_10"].fit(x10,y)

    first=_compare(screen,models)
    second=_compare(confirm,models)
    out["screen"]=first
    out["confirm"]=second
    if not (first["joint_objective"] and second["joint_objective"]):
        out["status"]="DEVELOPMENT_REJECTED"
        return out

    out["historical_calibration"]=_compare(historical,models)
    out["status"]="RESEARCH_CALIBRATION_ONLY"
    out["joint_objective_retrospective"]=out["historical_calibration"]["joint_objective"]
    return out


def main(argv: list[str] | None = None) -> int:
    from . import training
    from .config import BootstrapSettings

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=BootstrapSettings().bootstrap_dir)
    args=parser.parse_args(argv)
    try:
        root=args.root.resolve()
        context=training._verified_context(root)
        rows=training._read_rows_before_lockbox(context)
        dev_groups=set(context.plan.development_groups)
        cal_groups=set(context.plan.calibration_groups)
        report=evaluate_enriched_direction(
            [r for r in rows if training._group(r) in dev_groups],
            [r for r in rows if training._group(r) in cal_groups],
        )
        report["verified_feature_dataset_sha256"]=context.input_hashes[
            "derived/features.parquet"
        ]
        report["partition"]="VERIFIED_PRELOCKBOX_DEVELOPMENT_THEN_CALIBRATION"
    except (OSError, ValueError, training.TrainingError) as exc:
        report={
            "status":"DATASET_UNAVAILABLE_OR_UNVERIFIED",
            "error":str(exc),
            "model_saved":False,
            "ready_to_promote":False,
            "reserved_lockbox_opened":False,
        }
        print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
        return 2
    print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
