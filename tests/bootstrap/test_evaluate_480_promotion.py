"""Future volatility candidates must be evaluated at the ACTUAL 480s trade lock.

No test reads private user trading data or places orders.
"""
import pytest

from kalshi_edge.bootstrap import artifact, evaluate, training
from kalshi_edge.bootstrap.types import FeatureRow
def _metric(*, brier: float, log_loss: float, ece: float = .02):
    return artifact.MetricSnapshot(
        brier=brier, log_loss=log_loss, accuracy=.60, ece=ece, sharpness=.15,
    )


def _report(*, candidate=None, prior=None):
    return evaluate.BootstrapEvaluationReport(
        bundle_sha256="a"*64, dataset_sha256="b"*64,
        model_version=training.MODEL_VERSION, lockbox_start_ts_ns=300,
        lockbox_end_ts_ns=400, evaluated_rows=20, excluded_rows=0,
        feature_schema_match=True,
        leakage_audit=artifact.LeakageEvidence(passed=True,finding_count=0,finding_codes=()),
        required_ablations_present=True,
        metrics={
            "candidate": candidate or _metric(brier=.20,log_loss=.60),
            "kalshi_prior": prior or _metric(brier=.23,log_loss=.66),
        },
        comparison_rule=evaluate.PROMOTION_RULE_ID,
    )


def _row(ticker, seconds, timestamp):
    return FeatureRow(
        market_ticker=ticker,
        checkpoint_ts_ns=timestamp,
        label_yes=1,
        features={"seconds_remaining": float(seconds)},
        source_max_ts_ns={"binance":timestamp},
    )


def test_selection_of_480s_is_exact_and_unique_market_scoped():
    rows=[
        _row("a",240,100),_row("a",480,200),
        _row("a",479.7,201),_row("b",480,300),
        _row("b",240,400),_row("c",478.8,450),
        _row("d",float("nan"),500),
    ]
    selected=evaluate._one_checkpoint_per_market(rows,480)
    assert [row.market_ticker for row in selected]==["a","b"]
    assert selected[0].checkpoint_ts_ns==200
    assert all(abs(row.features["seconds_remaining"]-480)<=1 for row in selected)


def test_480_gate_rejects_more_confident_but_worse_probabilities():
    base=_report(
        candidate=_metric(brier=.15,log_loss=.44,ece=.02),
        prior=_metric(brier=.18,log_loss=.51,ece=.02),
    )
    good_240={
        "volatility_240_candidate":_metric(brier=.14,log_loss=.40,ece=.02),
        "volatility_240_incumbent":_metric(brier=.17,log_loss=.50,ece=.02),
        "volatility_240_kalshi":_metric(brier=.19,log_loss=.56,ece=.02),
        "incumbent_current":_metric(brier=.16,log_loss=.46,ece=.02),
    }
    bad_480={
        "volatility_480_candidate":_metric(brier=.19,log_loss=.56,ece=.02),
        "volatility_480_incumbent":_metric(brier=.17,log_loss=.50,ece=.02),
        "volatility_480_kalshi":_metric(brier=.18,log_loss=.53,ece=.02),
    }
    result=evaluate.promotion_decision(
        base.model_copy(update={"metrics":{**base.metrics,**good_240,**bad_480}})
    )
    assert result.promoted is False
    assert "VOLATILITY_480_INCUMBENT_NOT_BEATEN" in result.reason_codes


def test_480_gate_rejects_direction_accuracy_degradation_even_when_scores_better():
    base=_report(candidate=_metric(brier=.15,log_loss=.44,ece=.02),
                 prior=_metric(brier=.18,log_loss=.51,ece=.02))
    cand=_metric(brier=.14,log_loss=.40,ece=.02).model_copy(update={"accuracy":.50})
    incumbent=_metric(brier=.17,log_loss=.50,ece=.02).model_copy(update={"accuracy":.75})
    augmented=base.model_copy(update={"metrics":{
        **base.metrics,
        "volatility_480_candidate":cand,
        "volatility_480_incumbent":incumbent,
        "volatility_480_kalshi":_metric(brier=.19,log_loss=.56,ece=.02),
    }})
    decision=evaluate.promotion_decision(augmented)
    assert decision.promoted is False
    assert "VOLATILITY_480_INCUMBENT_ACCURACY_DEGRADED" in decision.reason_codes


def test_480_gate_cannot_silently_skip_missing_comparator():
    base=_report()
    partial=base.model_copy(update={"metrics":{
        **base.metrics,
        "volatility_480_candidate":_metric(brier=.12,log_loss=.38),
    }})
    verdict=evaluate.promotion_decision(partial)
    assert verdict.promoted is False
    assert "VOLATILITY_480_BENCHMARK_MISSING" in verdict.reason_codes


def test_480_gate_can_pass_when_compared_with_same_horizon_baselines():
    base=_report(candidate=_metric(brier=.15,log_loss=.44,ece=.02),
                 prior=_metric(brier=.18,log_loss=.51,ece=.02))
    augmented=base.model_copy(update={"metrics":{
        **base.metrics,
        "incumbent_current":_metric(brier=.16,log_loss=.46,ece=.02),
        "volatility_480_candidate":_metric(brier=.14,log_loss=.40,ece=.02).model_copy(update={"accuracy":.80}),
        "volatility_480_incumbent":_metric(brier=.17,log_loss=.50,ece=.02).model_copy(update={"accuracy":.75}),
        "volatility_480_kalshi":_metric(brier=.19,log_loss=.56,ece=.02),
    }})
    augmented=augmented.model_copy(update={"decision_metrics":{
        "volatility_480_candidate":evaluate.GateSnapshot(
            total_markets=80,qualified_markets=26,qualified_correct=25,
            minimum_confidence=.69,
        ),
        "volatility_480_incumbent":evaluate.GateSnapshot(
            total_markets=80,qualified_markets=20,qualified_correct=18,
            minimum_confidence=.69,
        ),
    }})
    assert evaluate.promotion_decision(augmented).promoted is True


def test_480_subsample_requires_minimum_distinct_markets_before_scoring():
    rows=[_row(f"m{i}",480,1000+i) for i in range(79)]
    selected=evaluate._one_checkpoint_per_market(rows,480)
    assert len(selected)==79
    with pytest.raises(evaluate.EvaluationError,match="80.*480"):
        evaluate._require_horizon_sample(selected,seconds=480,minimum=80)
    selected.append(_row("extra",480,1080))
    evaluate._require_horizon_sample(selected,seconds=480,minimum=80)


def test_full_retrain_480_checkpoint_matches_immutable_late_lock_window():
    # Live final can lock no earlier than 480s and at most 2s after.
    rows = [
        _row("late-valid", 478.1, 1000),
        _row("too-late", 477.9, 1100),
        _row("pre-lock", 480.1, 1200),
    ]
    selected = evaluate._one_checkpoint_per_market(rows, 480)
    assert [r.market_ticker for r in selected] == ["late-valid"]

