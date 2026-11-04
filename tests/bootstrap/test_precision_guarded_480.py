"""Strict out-of-fold risk-limited rescue: no calibration-label threshold search."""
from __future__ import annotations
from pathlib import Path
import math
import pytest

from kalshi_edge.bootstrap.types import FeatureRow

NS=1_000_000_000
START=1_900_000_000 * NS


def fake(i:int, *, seconds:float=480, ticker:str|None=None, extras:dict|None=None,
         positive:bool|None=None) -> FeatureRow:
    ts=START+i*900*NS
    up=i%2==0
    outcome = int(up != (i%11==0 or i%17==0)) if positive is None else int(positive)
    midpoint = (.74 if up else .26) if i%9 else (.3 if up else .7)
    features={
        "seconds_remaining":seconds,
        "btc_normalized_distance":1.8 if up else -1.8,
        "btc_realized_vol_60s":.0002+(i%9)*.00004,
        "btc_realized_vol_300s":.00042+(i%7)*.000015,
        "btc_return_60s":(.0003 if up else -.0003)*(1+i%5),
        "btc_return_60s_available":1.0,
        "kalshi_quote_available":1.0,
        "kalshi_yes_bid":midpoint-.025,
        "kalshi_yes_ask":midpoint+.025,
        "kalshi_mid":midpoint,
        "kalshi_spread":.05,
        "kalshi_quote_staleness_seconds":1.0+(i%8),
    }
    features.update(extras or {})
    ticker=ticker or f"KXBTC15M-RISK-{i}"
    return FeatureRow(
        market_ticker=ticker,split_group_id=ticker,
        checkpoint_ts_ns=ts,label_yes=outcome,features=features,
        source_max_ts_ns={"binance":ts-NS,"kalshi_candles":ts-2*NS},
    )


def sample(n:int=320):
    return [fake(i) for i in range(n)], [fake(i) for i in range(n,n+60)]


def policy_example(*, good:bool=True):
    # Baseline: 90 correct / 100 qualifying; low-confidence rescue
    # segment: 100 excellent, then 40 noisy.
    y=[1]*90 + [0]*10
    p=[.95]*100
    meta=[.1]*100
    y += [1]*(100 if good else 80) + [0]*(0 if good else 20)
    p += [.7]*100
    meta += [.93]*100
    y += [1]*20 + [0]*20
    p += [.7]*40
    meta += [.88]*40
    return y,p,meta


def test_only_training_fold_may_select_guard_and_noisy_rescue_rejected():
    from kalshi_edge.bootstrap.precision_guarded_480 import choose_guarded_threshold

    good=choose_guarded_threshold(*policy_example())
    assert good["status"]=="THRESHOLD_SELECTED"
    assert good["selected_threshold"]==.925
    assert good["selected_rescue_correct"]==100
    assert good["selected_rescue_incorrect"]==0
    assert good["selected_rescue_wilson_lower"] > .9
    assert good["baseline"]["accuracy"]==pytest.approx(.9)
    assert len(good["grid"])==5

    bad=choose_guarded_threshold(*policy_example(good=False))
    assert bad["status"]=="NO_SAFE_DEVELOPMENT_THRESHOLD"
    assert bad["selected_threshold"] is None


def test_confirmation_can_reject_threshold_independently():
    from kalshi_edge.bootstrap.precision_guarded_480 import confirm_guard

    y,p,meta=policy_example()
    confirmed=confirm_guard(y,p,meta,.925)
    assert confirmed["passes"] is True
    assert confirmed["rescues"]["qualified"]==100
    assert confirmed["rescues"]["correct"]==100
    assert confirmed["hybrid"]["accuracy"]>confirmed["baseline"]["accuracy"]

    y,p,meta=policy_example(good=False)
    rejected=confirm_guard(y,p,meta,.925)
    assert rejected["passes"] is False
    assert rejected["rescues"]["incorrect"]==20
    assert rejected["hybrid"]["accuracy"]<rejected["baseline"]["accuracy"]


def test_research_uses_four_expanding_development_folds_and_never_deploys():
    from kalshi_edge.bootstrap.precision_guarded_480 import evaluate_precision_guard
    a,b=sample()
    r=evaluate_precision_guard(a,b)
    assert r["direction_fit_markets"]==320
    assert r["evaluation_markets"]==60
    assert r["oof_folds"]
    assert len(r["oof_folds"])==4
    assert r["oof_meta_fit_markets"]==80
    assert r["threshold_selection_markets"]==40
    assert r["internal_confirmation_markets"]==40
    assert r["training_last_ns"]+900*NS <= r["evaluation_first_ns"]
    for fold in r["oof_folds"]:
        assert fold["fit_last_ns"]+900*NS<=fold["oof_first_ns"]
    assert r["status"] in {
        "NO_SAFE_DEVELOPMENT_THRESHOLD",
        "FAILED_INTERNAL_CONFIRMATION",
        "RESEARCH_CALIBRATION_ONLY",
    }
    assert r["safety"]["model_saved"] is False
    assert r["safety"]["ready_to_promote"] is False
    assert r["safety"]["live_trading_changed"] is False
    assert r["safety"]["reserved_lockbox_opened"] is False


def test_cutoff_unchanged_when_future_labels_mutated():
    from kalshi_edge.bootstrap.precision_guarded_480 import evaluate_precision_guard
    a,b=sample()
    first=evaluate_precision_guard(a,b)
    changed=[r.model_copy(update={"label_yes":1-r.label_yes}) for r in b]
    second=evaluate_precision_guard(a,changed)
    assert first["threshold_selection"]==second["threshold_selection"]
    assert first["internal_confirmation"]==second["internal_confirmation"]
    assert first["selected_threshold"]==second["selected_threshold"]


def test_time_overlap_and_prior_period_must_fail_closed():
    from kalshi_edge.bootstrap.precision_guarded_480 import evaluate_precision_guard
    a,b=sample()
    assert evaluate_precision_guard(b,a)["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_precision_guard(a,[a[-1],*b])["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"
    assert evaluate_precision_guard(a[:79],b)["status"]=="INSUFFICIENT_DIRECTION_TRAINING"
    assert evaluate_precision_guard(a,b[:19])["status"]=="INSUFFICIENT_EVALUATION"
    reused=[b[0].model_copy(update={
        "market_ticker":a[0].market_ticker,
        "split_group_id":a[0].market_ticker,
    }),*b[1:]]
    assert evaluate_precision_guard(a,reused)["status"]=="LEAKAGE_OR_OVERLAPPING_PERIODS"


def test_480s_only_and_market_duplicates_and_stale_quotes_excluded():
    from kalshi_edge.bootstrap.precision_guarded_480 import evaluate_precision_guard
    a,b=sample()
    r=evaluate_precision_guard(
        [*a,a[10],fake(500,seconds=240),
         fake(501,extras={"kalshi_quote_available":0.0})],
        b,
    )
    assert r["direction_fit_markets"]==319
    assert r["development_exclusions"]["duplicate_rows"]==2
    assert r["development_exclusions"]["wrong_checkpoint"]==1
    assert r["development_exclusions"]["invalid_or_stale_quote"]==1


def test_no_model_promoted_on_one_class_direction_or_correctness_labels():
    from kalshi_edge.bootstrap.precision_guarded_480 import evaluate_precision_guard
    a,b=sample()
    same=[r.model_copy(update={"label_yes":1}) for r in a]
    assert evaluate_precision_guard(same,b)["status"]=="SINGLE_CLASS_DIRECTION_TARGET"


def test_invalid_paired_gate_input_refused():
    from kalshi_edge.bootstrap.precision_guarded_480 import choose_guarded_threshold
    with pytest.raises(ValueError):
        choose_guarded_threshold([1],[float("nan")],[.9])
    with pytest.raises(ValueError):
        choose_guarded_threshold([1,0],[.9],[.9])
