"""Post-training one-time 480s paired audit; never replays a sealed lockbox."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap import promotion_audit


def test_paired_outcome_counts_and_qualifying_overlap_are_matched_by_market():
    tickers = ("m1", "m2", "m3", "m4", "m5")
    labels = (1, 0, 1, 0, 0)
    candidate = (.95, .05, .2, .9, .95)
    incumbent = (.95, .95, .95, .2, .9)
    audit = promotion_audit.paired_480_evidence(
        tickers, labels, candidate, incumbent, resamples=300, seed=42,
    )
    assert audit["market_count"] == 5
    assert audit["both_correct"] == 1
    assert audit["candidate_only_correct"] == 1
    assert audit["incumbent_only_correct"] == 2
    assert audit["both_wrong"] == 1
    assert audit["candidate_qualified"] == 4
    assert audit["incumbent_qualified"] == 4
    assert audit["both_qualified"] == 3
    assert audit["candidate_only_qualified"] == 1
    assert audit["incumbent_only_qualified"] == 1
    assert audit["neither_qualified"] == 0
    assert audit["accuracy_delta_pp"] == pytest.approx(-20.0)
    assert audit["mcnemar_exact_p"] == 1.0
    assert audit["confidence_interval_95_pp"][0] <= -20.0 <= audit["confidence_interval_95_pp"][1]
    assert audit["paired_uncertainty"]["method"] == "paired_market_bootstrap"
    assert audit["trade_economics"]["status"] == "UNAVAILABLE"
    assert "fees" in audit["trade_economics"]["reason"].lower()


def test_identical_models_have_zero_paired_delta_and_no_fake_significance():
    tickers = tuple(f"m{i}" for i in range(40))
    labels = tuple(i % 2 for i in range(40))
    p = tuple(.7 if i % 3 else .2 for i in range(40))
    audit = promotion_audit.paired_480_evidence(tickers, labels, p, p, resamples=250)
    assert audit["accuracy_delta_pp"] == 0.0
    assert audit["confidence_interval_95_pp"] == [0.0, 0.0]
    assert audit["mcnemar_exact_p"] == 1.0
    assert audit["candidate_only_correct"] == 0
    assert audit["incumbent_only_correct"] == 0
    assert audit["paired_uncertainty"]["excludes_zero"] is False


@pytest.mark.parametrize("bad_case", ["duplicate", "invalid_probability", "mismatch", "bad_label"])
def test_paired_audit_fails_closed_on_unmatched_or_corrupted_evidence(bad_case):
    markets = ["a", "b"]
    labels = [1, 0]
    candidate = [.9, .1]
    incumbent = [.8, .2]
    if bad_case == "duplicate":
        markets[1] = "a"
    elif bad_case == "invalid_probability":
        candidate[0] = float("nan")
    elif bad_case == "mismatch":
        incumbent.pop()
    else:
        labels[0] = 2
    with pytest.raises(ValueError):
        promotion_audit.paired_480_evidence(markets, labels, candidate, incumbent)


def _old_report():
    """Legacy, previously finalized report contains only aggregate gate evidence."""
    def metric(acc, brier, loss, ece):
        return {"accuracy": acc, "brier": brier, "log_loss": loss, "ece": ece}
    return {
        "report": {
            "requires_retrain_480_gate": True,
            "metrics": {
                "retrain_480_candidate": metric(.755245, .168625, .507704, .047124),
                "retrain_480_incumbent": metric(.755245, .168670, .507956, .048635),
                "candidate": metric(.761072, .154091, .457706, .024836),
                "incumbent_current": metric(.761239, .154076, .457848, .023927),
            },
            "decision_metrics": {
                "retrain_480_candidate": {
                    "total_markets": 858, "qualified_markets": 321,
                    "qualified_correct": 287, "minimum_confidence": .69,
                },
                "retrain_480_incumbent": {
                    "total_markets": 858, "qualified_markets": 318,
                    "qualified_correct": 284, "minimum_confidence": .69,
                },
            },
        },
        "decision": {
            "promoted": False,
            "reason_codes": [
                "INCUMBENT_MODEL_NOT_BEATEN",
                "RETRAIN_480_INCUMBENT_ACCURACY_NOT_IMPROVED",
            ],
        },
    }


def test_existing_sealed_evaluation_gains_aggregate_explanation_without_replay():
    summary = promotion_audit.summarize_saved_report(_old_report())
    assert summary["status"] == "AGGREGATES_ONLY"
    assert summary["market_count"] == 858
    assert summary["overall_480_accuracy_delta_pp"] == pytest.approx(0.0)
    assert summary["480_brier_delta"] == pytest.approx(-.000045)
    assert summary["480_log_loss_delta"] == pytest.approx(-.000252)
    assert summary["480_ece_delta"] == pytest.approx(-.001511)
    assert summary["coverage_delta_pp"] == pytest.approx(100 * 3 / 858)
    assert summary["paired_480"] is None
    assert "not recorded" in summary["paired_status"].lower()
    assert summary["trade_economics"]["status"] == "UNAVAILABLE"
    assert summary["decision_unchanged"] is True


def test_new_report_exposes_paired_evidence_without_affecting_prior_decision():
    from kalshi_edge.bootstrap import evaluate
    from kalshi_edge.bootstrap.metrics import probability_metrics
    old = _old_report()
    labels = [1, 0, 1, 0]
    candidate = [.95, .05, .05, .05]
    incumbent = [.05, .95, .95, .05]
    report = old["report"]
    report["paired_480_audit"] = promotion_audit.paired_480_evidence(
        ["a", "b", "c", "d"], labels, candidate, incumbent, resamples=100,
    )
    for key, ps in (("candidate", candidate), ("incumbent", incumbent)):
        report["metrics"]["retrain_480_" + key] = probability_metrics(
            labels, ps,
        ).model_dump(mode="json")
        report["decision_metrics"]["retrain_480_" + key] = evaluate.checkpoint_gate_snapshot(
            labels, ps,
        ).model_dump(mode="json")
    summary = promotion_audit.summarize_saved_report(old)
    assert summary["status"] == "PAIRED_AVAILABLE"
    assert summary["paired_480"]["market_count"] == 4
    assert summary["decision_unchanged"] is True
    assert old["decision"]["promoted"] is False


def test_readonly_report_reader_refuses_outside_path_and_does_not_modify_report(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    path = reports / ("evaluation-" + "a" * 64 + ".json")
    raw = json.dumps(_old_report(), sort_keys=True)
    path.write_text(raw, encoding="utf-8")
    summary = promotion_audit.load_report_audit(tmp_path, path)
    assert summary["market_count"] == 858
    assert path.read_text(encoding="utf-8") == raw

    outside = tmp_path.parent / "outside-evaluation.json"
    outside.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        promotion_audit.load_report_audit(tmp_path, outside)


def test_train_page_has_readonly_paired_evidence_section():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert 'id="training-promotion-audit"' in PAGE
    assert 'run.promotion_audit' in PAGE
    assert 'paired_480' in PAGE
    assert "confidence_interval_95_pp" in PAGE
    assert "trade_economics" in PAGE
    assert "not recorded" in PAGE.lower()


def test_existing_finalized_training_run_displays_readonly_audit(tmp_path, monkeypatch):
    """No model replay; existing evaluation JSON is simply read once for status."""
    import time
    from kalshi_edge.bootstrap import training_cycle
    from kalshi_edge.bootstrap import model_identity

    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / ("evaluation-" + "a" * 64 + ".json")
    report.write_text(json.dumps(_old_report()), encoding="utf-8")
    training_cycle._write_worker_state(
        tmp_path, stage="COMPLETE", mode="baseline", round_number=0,
        decision="VALIDATION_FAILED", promoted=False,
        evaluation_report=str(report),
    )

    controller = training_cycle.TrainingController(tmp_path, project_root=tmp_path)
    controller._cached = {"ready": False, "status": "WAITING"}
    controller._cached_at = time.monotonic()
    controller._forward_cached = {"ready": False, "status": "WAITING"}
    controller._forward_at = time.monotonic()
    controller._baseline_cached = {"ready": False, "status": "WAITING"}
    controller._baseline_at = time.monotonic()
    monkeypatch.setattr(model_identity, "current_model_identity", lambda root: {})
    state_before = (tmp_path / "training_runs" / "current.json").read_bytes()
    report_before = report.read_bytes()

    result = controller.status()
    audit = result["run"]["promotion_audit"]
    assert audit["status"] == "AGGREGATES_ONLY"
    assert audit["market_count"] == 858
    assert audit["overall_480_accuracy_delta_pp"] == 0.0
    assert (tmp_path / "training_runs" / "current.json").read_bytes() == state_before
    assert report.read_bytes() == report_before


def test_original_one_time_lockbox_evaluation_seals_paired_480_evidence(tmp_path, monkeypatch):
    """All 80 matched outcomes are captured in the original score/pass, not replayed."""
    from types import SimpleNamespace
    from kalshi_edge.bootstrap import evaluate, artifact, training
    from kalshi_edge.bootstrap.metrics import probability_metrics
    from kalshi_edge.bootstrap.types import FeatureRow
    from kalshi_edge.bootstrap import live

    root = tmp_path
    (root / "models").mkdir()
    (root / "models" / "default.json").write_text("{}", encoding="utf-8")
    path = root / "models" / "experiments" / ("a" * 64 + ".json")
    bounds = SimpleNamespace(lockbox_start_ts_ns=1, lockbox_end_ts_ns=100000)
    challenger_bundle = SimpleNamespace(bundle_sha256="a" * 64,
                                        boundaries=bounds, input_hashes={})
    incumbent_bundle = SimpleNamespace(identity="current_model")
    rows = tuple(
        FeatureRow(
            market_ticker=f"KXBTC15M-{i:04d}", checkpoint_ts_ns=100 + i,
            label_yes=i % 2, features={"seconds_remaining": 480.0},
            source_max_ts_ns={"kalshi": 100 + i},
        )
        for i in range(80)
    )
    labels = [row.label_yes for row in rows]
    challenger_p = [.95 if value else .05 for value in labels]
    incumbent_p = list(challenger_p)
    incumbent_p[0] = .95  # incumbent gets one market wrong
    calls = {"load": 0, "one_time": 0, "finalized": 0}

    monkeypatch.setattr(artifact, "load_model_bundle", lambda p: challenger_bundle)
    monkeypatch.setattr(evaluate, "_require_untouched_lockbox_interval", lambda *args: None)

    def read_lockbox(*args, **kwargs):
        calls["load"] += 1
        return rows
    monkeypatch.setattr(training, "load_lockbox_rows", read_lockbox)
    monkeypatch.setattr(live, "load_default_bundle", lambda root: incumbent_bundle)

    def predictions(bundle, cohort):
        selected = [row.market_ticker for row in cohort]
        assert selected == [row.market_ticker for row in rows]
        return {
            "candidate": challenger_p if bundle is challenger_bundle else incumbent_p,
        }
    monkeypatch.setattr(evaluate, "_predict_bundle_rows", predictions)

    def first_evaluation(bundle, cohort):
        calls["one_time"] += 1
        assert cohort == rows
        return evaluate.BootstrapEvaluationReport(
            bundle_sha256="a" * 64, dataset_sha256="b" * 64,
            model_version=training.MODEL_VERSION,
            lockbox_start_ts_ns=1, lockbox_end_ts_ns=100000,
            evaluated_rows=80, excluded_rows=0, feature_schema_match=True,
            leakage_audit=artifact.LeakageEvidence(
                passed=True, finding_count=0, finding_codes=(),
            ),
            required_ablations_present=True,
            metrics={
                "candidate": artifact.metric_snapshot(
                    probability_metrics(labels, challenger_p),
                ),
                "kalshi_prior": artifact.metric_snapshot(
                    probability_metrics(labels, [.5] * len(labels)),
                ),
            },
            comparison_rule=evaluate.PROMOTION_RULE_ID,
        )
    monkeypatch.setattr(evaluate, "evaluate_lockbox", first_evaluation)

    def finalize(root, path, report):
        calls["finalized"] += 1
        paired = report.paired_480_audit
        assert paired["market_count"] == 80
        assert paired["candidate_only_correct"] == 1
        assert paired["incumbent_only_correct"] == 0
        assert paired["candidate_qualified"] == 80
        assert paired["incumbent_qualified"] == 80
        assert report.requires_retrain_480_gate is True
        sealed = {
            "report": report.model_dump(mode="json"),
            "decision": {"promoted": False, "reason_codes": ["TEST_REJECTED"]},
        }
        summary = promotion_audit.summarize_saved_report(sealed)
        assert summary["status"] == "PAIRED_AVAILABLE"
        assert summary["paired_480"]["candidate_only_correct"] == 1
        return SimpleNamespace(report_path=tmp_path / "evaluation.json", promoted=False)
    monkeypatch.setattr(evaluate, "finalize_evaluation", finalize)
    evaluate.run_lockbox_evaluation(root, path)
    assert calls == {"load": 1, "one_time": 1, "finalized": 1}
