"""Full compatible-model retraining uses independent 480s gates before promotion."""
from pathlib import Path
from types import SimpleNamespace

from kalshi_edge.bootstrap import artifact, evaluate, training, training_cycle


def _metric(brier, loss, accuracy=.8):
    return artifact.MetricSnapshot(
        brier=brier,log_loss=loss,accuracy=accuracy,ece=.02,sharpness=.15,
    )


def _report(*,cq=50,cc=48,iq=43,ic=40,with_480=True):
    metrics={
        "candidate":_metric(.12,.39,.8),
        "kalshi_prior":_metric(.20,.55,.7),
        "incumbent_current":_metric(.17,.50,.75),
    }
    gate={}
    if with_480:
        metrics.update({
            "retrain_480_candidate":_metric(.13,.41,.80),
            "retrain_480_incumbent":_metric(.17,.51,.75),
            "retrain_480_kalshi":_metric(.20,.55,.65),
        })
        gate={
            "retrain_480_candidate":evaluate.GateSnapshot(
                total_markets=100,qualified_markets=cq,qualified_correct=cc,
                minimum_confidence=.69,
            ),
            "retrain_480_incumbent":evaluate.GateSnapshot(
                total_markets=100,qualified_markets=iq,qualified_correct=ic,
                minimum_confidence=.69,
            ),
        }
    return evaluate.BootstrapEvaluationReport(
        bundle_sha256="a"*64,dataset_sha256="b"*64,
        model_version=training.MODEL_VERSION,lockbox_start_ts_ns=300,
        lockbox_end_ts_ns=400,evaluated_rows=100,excluded_rows=0,
        feature_schema_match=True,
        leakage_audit=artifact.LeakageEvidence(
            passed=True,finding_count=0,finding_codes=(),
        ),
        required_ablations_present=True,
        metrics=metrics,decision_metrics=gate,
        comparison_rule=evaluate.PROMOTION_RULE_ID,
        requires_retrain_480_gate=True,
    )


def test_single_train_now_prefers_compatible_full_retrain_over_research_when_eligible():
    assert training_cycle._select_training_mode(
        legacy={"ready":False,"progress_percent":40},
        forward={"ready":True,"eligible_480_markets":303},
        baseline={"ready":True,"fresh_evaluation_markets":472},
    )=="baseline"


def test_legacy_ready_takes_precedence_over_full_retrain():
    assert training_cycle._select_training_mode(
        legacy={"ready":True,"mode":"mistake"},
        forward={"ready":True},baseline={"ready":True},
    )=="mistake"


def test_full_retrain_requires_verified_independent_fresh_lockbox(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import training_cycle
    (tmp_path/"models").mkdir()
    (tmp_path/"models"/"default.json").write_text("{}")
    monkeypatch.setattr(training_cycle.training, "_verified_context",
                        lambda root: SimpleNamespace(
                            plan=SimpleNamespace(
                                development_groups=tuple(range(100)),
                                calibration_groups=tuple(range(20)),
                                lockbox_groups=tuple(range(472)),
                            ),
                        ))
    result=training_cycle.inspect_baseline_training_readiness(tmp_path)
    assert result["ready"] is True
    assert result["fresh_evaluation_markets"]==472
    assert result["mode"]=="baseline"
    assert result["live_model_changed"] is False


def test_full_retrain_uses_compatible_existing_training_and_actual_promotion(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import training_cycle
    monkeypatch.setattr(training_cycle,"inspect_baseline_training_readiness",
                        lambda root:{"ready":True})
    monkeypatch.setattr(training_cycle.training,"_verified_context",
                        lambda root: SimpleNamespace())
    calls=[]
    def fake_train(root,settings,*,git_sha,mistakes_only=False,correct_only=False):
        calls.append(("fit",mistakes_only,correct_only))
        return SimpleNamespace(experiment_bundle_sha256="a"*64,
                               experiment_path=tmp_path/"experiment.json")
    def fake_evaluate(root,path,*,progress=None):
        calls.append(("lockbox",path))
        return SimpleNamespace(
            decision=SimpleNamespace(promoted=True,reason_codes=()),
            report_path=tmp_path/"evaluation.json",
        )
    monkeypatch.setattr(evaluate,"train_experiment",fake_train)
    monkeypatch.setattr(evaluate,"run_lockbox_evaluation",fake_evaluate)
    result=training_cycle.run_training_worker(
        tmp_path,mode="baseline",round_number=0,git_sha="b"*40,
    )
    assert result==0
    state=training_cycle._run_state(tmp_path)
    assert state["decision"]=="PROMOTED"
    assert state["promoted"] is True
    assert state["progress_percent"]==100
    assert calls[0]==("fit",False,False)
    assert calls[1][0]=="lockbox"


def test_full_retrain_480_rejects_high_accuracy_but_fewer_forecasts():
    report=_report(cq=15,cc=15,iq=43,ic=40)
    verdict=evaluate.promotion_decision(report)
    assert verdict.promoted is False
    assert "RETRAIN_480_COVERAGE_NOT_INCREASED" in verdict.reason_codes


def test_full_retrain_480_can_pass_only_complete_better_accuracy_and_coverage():
    report=_report()
    assert evaluate.promotion_decision(report).promoted is True
    incomplete=_report(with_480=False)
    # A retraining report that claims an incumbent but omits fresh 480s
    # gate evidence must never be allowed to replace a live model.
    assert evaluate.promotion_decision(incomplete).promoted is False
    assert "RETRAIN_480_CONFIDENCE_GATE_MISSING" in evaluate.promotion_decision(incomplete).reason_codes


def test_full_retrain_480_rejects_worse_accuracy_even_with_more_trades():
    report=_report(cq=60,cc=45,iq=43,ic=40)
    verdict=evaluate.promotion_decision(report)
    assert verdict.promoted is False
    assert "RETRAIN_480_QUALIFIED_ACCURACY_DEGRADED" in verdict.reason_codes
