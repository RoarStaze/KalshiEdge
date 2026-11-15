"""Manual promotion is explicit, reversible, and never claims auto gates passed."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap import artifact, evaluate, live, manual_promotion, promotion_audit, training


def _metric(acc: float, brier: float, log_loss: float, ece: float = .02):
    return artifact.MetricSnapshot(
        accuracy=acc, brier=brier, log_loss=log_loss, ece=ece, sharpness=.15
    )


def _bundle():
    raw=b"rejected-but-hash-verified-model"
    return artifact.ModelBundle(
        stage="experiment", model_version=training.MODEL_VERSION,
        git_sha="a"*40, random_seed=73115,
        feature_schema_version=training.FEATURE_SCHEMA_VERSION,
        feature_names=("btc_close","strike","seconds_remaining"),
        boundaries=artifact.SplitBoundaries(
            train_start_ts_ns=100, train_end_ts_ns=200,
            calibration_start_ts_ns=201, calibration_end_ts_ns=250,
            lockbox_start_ts_ns=300, lockbox_end_ts_ns=400,
        ),
        input_hashes={"derived/features.parquet":"b"*64},
        training_config_hash="c"*64,
        library_versions={"python":"3.13"},
        components=(artifact.ComponentIdentity(
            name="kalshi_prior",kind="benchmark",identity="kalshi_mid",weight=1.0,
        ),),
        calibration_method="identity",
        leakage_audit=artifact.LeakageEvidence(
            passed=True,finding_count=0,finding_codes=(),
        ),
        metrics={"candidate":_metric(.75,.18,.52)},
        ablations=(),
        serialized_pipeline_b64=base64.b64encode(raw).decode(),
        serialized_pipeline_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _setup(root: Path):
    root=root.resolve()
    experiment_path=artifact.save_model_bundle(
        _bundle(),root/"models"/"experiments",
    )
    original=_bundle().model_copy(update={
        "stage":"promoted",
        "source_experiment_sha256":"d"*64,
        "promotion_rule":evaluate.LEGACY_PROMOTION_RULE_ID,
    })
    old_path=artifact.save_model_bundle(original,root/"models"/"promoted")
    pointer={
        "model_number":1,
        "previous_model_hash":None,
        "bundle_sha256":old_path.stem,
        "path":old_path.relative_to(root).as_posix(),
        "source_experiment_sha256":"d"*64,
        "promotion_rule":evaluate.LEGACY_PROMOTION_RULE_ID,
    }
    evaluate._atomic_json(root/"models"/"default.json",pointer)
    labels=[1]*100
    candidate=[.9]*54+[.6]*21+[.1]*6+[.4]*19
    incumbent=[.9]*62+[.6]*12+[.1]*8+[.4]*18
    paired=promotion_audit.paired_480_evidence(
        [f"M{i:03d}" for i in range(100)],labels,candidate,incumbent,
        resamples=20,
    )
    assert paired["candidate_qualified"]==60
    assert paired["incumbent_qualified"]==70
    assert paired["accuracy_delta_pp"]==1.0

    c_gate=evaluate.checkpoint_gate_snapshot(labels,candidate)
    i_gate=evaluate.checkpoint_gate_snapshot(labels,incumbent)
    report=evaluate.BootstrapEvaluationReport(
        bundle_sha256=experiment_path.stem,
        dataset_sha256="b"*64,
        model_version=training.MODEL_VERSION,
        lockbox_start_ts_ns=300, lockbox_end_ts_ns=400,
        evaluated_rows=100,excluded_rows=0,
        feature_schema_match=True,
        leakage_audit=artifact.LeakageEvidence(
            passed=True,finding_count=0,finding_codes=(),
        ),
        required_ablations_present=True,
        metrics={
            "candidate":_metric(.77,.16,.47),
            "incumbent_current":_metric(.76,.15,.46),
            "kalshi_prior":_metric(.5,.25,.70,ece=.03),
            "retrain_480_candidate":_metric(.75,.18,.52),
            "retrain_480_incumbent":_metric(.74,.17,.51),
            "retrain_480_kalshi":_metric(.5,.25,.70,ece=.03),
        },
        decision_metrics={
            "retrain_480_candidate":c_gate,
            "retrain_480_incumbent":i_gate,
        },
        requires_retrain_480_gate=True,
        paired_480_audit=paired,
        comparison_rule=evaluate.LEGACY_PROMOTION_RULE_ID,
    )
    decision=evaluate.promotion_decision(report)
    assert set(decision.reason_codes)=={
        "INCUMBENT_MODEL_NOT_BEATEN",
        "RETRAIN_480_INCUMBENT_NOT_BEATEN",
        "RETRAIN_480_COVERAGE_NOT_INCREASED",
    }
    report_path=root/"reports"/f"evaluation-{experiment_path.stem}.json"
    evaluate._atomic_json(report_path,{
        "report":report.model_dump(mode="json"),
        "decision":decision.model_dump(mode="json"),
    })
    run={
        "stage":"COMPLETE","mode":"baseline","decision":"VALIDATION_FAILED",
        "experiment_sha256":experiment_path.stem,
        "evaluation_report":str(report_path),
        "promoted":False,
    }
    evaluate._atomic_json(root/"training_runs"/"current.json",run)
    return experiment_path.stem, pointer, report_path


def test_preview_is_readonly_and_reports_accuracy_coverage_and_rejection(tmp_path):
    digest,old,report_path=_setup(tmp_path)
    before=(tmp_path/"models"/"default.json").read_bytes()
    summary=manual_promotion.preview(tmp_path)
    assert summary["eligible"] is True
    assert summary["experiment_sha256"]==digest
    assert summary["paired_accuracy_delta_pp"] == pytest.approx(1.0, abs=1e-12)
    assert summary["coverage_delta_pp"]==-10.0
    assert set(summary["rejection_codes"])=={
        "INCUMBENT_MODEL_NOT_BEATEN",
        "RETRAIN_480_INCUMBENT_NOT_BEATEN",
        "RETRAIN_480_COVERAGE_NOT_INCREASED",
    }
    assert (tmp_path/"models"/"default.json").read_bytes()==before
    assert report_path.exists()


def test_manual_activation_is_optin_auditable_and_reversible(tmp_path):
    digest,previous,report_path=_setup(tmp_path)
    report_before=report_path.read_bytes()
    run_before=(tmp_path/"training_runs"/"current.json").read_bytes()
    with pytest.raises(ValueError,match="acknowledg"):
        manual_promotion.activate(tmp_path,expected_experiment_sha=digest,acknowledge=False)
    with pytest.raises(ValueError,match="expected"):
        manual_promotion.activate(tmp_path,expected_experiment_sha="f"*64,acknowledge=True)
    assert json.loads((tmp_path/"models"/"default.json").read_text())==previous

    result=manual_promotion.activate(tmp_path,expected_experiment_sha=digest,acknowledge=True)
    assert result["status"]=="MANUALLY_ACTIVATED"
    assert result["model_number"]==2
    assert result["automatic_promotion_passed"] is False
    pointer=json.loads((tmp_path/"models"/"default.json").read_text())
    assert pointer["promotion_rule"]==manual_promotion.MANUAL_RULE_ID
    assert pointer["previous_model_hash"]==previous["bundle_sha256"]
    bundle=live.load_default_bundle(tmp_path)
    assert bundle.promotion_rule==manual_promotion.MANUAL_RULE_ID
    assert bundle.source_experiment_sha256==digest
    assert set(bundle.promotion_reason_codes)==set(manual_promotion.ALLOWED_REASONS)
    assert report_path.read_bytes()==report_before
    assert (tmp_path/"training_runs"/"current.json").read_bytes()==run_before
    assert manual_promotion.activate(tmp_path,expected_experiment_sha=digest,acknowledge=True)["status"]=="ALREADY_ACTIVE"
    with pytest.raises(ValueError,match="expected"):
        manual_promotion.rollback(tmp_path,expected_active_hash="e"*64,acknowledge=True)
    rolled=manual_promotion.rollback(
        tmp_path,expected_active_hash=bundle.bundle_sha256,acknowledge=True,
    )
    assert rolled["status"]=="ROLLED_BACK"
    assert json.loads((tmp_path/"models"/"default.json").read_text())==previous
    assert live.load_default_bundle(tmp_path).bundle_sha256==previous["bundle_sha256"]


def test_reject_modified_report_and_security_failed_bundles(tmp_path):
    digest,prior,report_path=_setup(tmp_path)
    payload=json.loads(report_path.read_text())
    payload["decision"]["reason_codes"].append("LEAKAGE_AUDIT_FAILED")
    evaluate._atomic_json(report_path,payload)
    with pytest.raises(ValueError,match="decision|report|mismatch"):
        manual_promotion.preview(tmp_path)
    assert json.loads((tmp_path/"models"/"default.json").read_text())==prior


def test_reject_unapproved_gate_and_no_paired_data(tmp_path):
    digest,prior,report_path=_setup(tmp_path)
    payload=json.loads(report_path.read_text())
    payload["report"]["paired_480_audit"]=None
    evaluate._atomic_json(report_path,payload)
    with pytest.raises(ValueError,match="paired|audit"):
        manual_promotion.preview(tmp_path)

    digest,prior,report_path=_setup(tmp_path)
    payload=json.loads(report_path.read_text())
    payload["report"]["feature_schema_match"]=False
    updated=evaluate.BootstrapEvaluationReport.model_validate(payload["report"])
    payload["decision"]=evaluate.promotion_decision(updated).model_dump(mode="json")
    evaluate._atomic_json(report_path,payload)
    with pytest.raises(ValueError,match="reason|integrity|schema|safety"):
        manual_promotion.preview(tmp_path)
    assert json.loads((tmp_path/"models"/"default.json").read_text())==prior


def test_reject_incomplete_run_and_wrong_report_path(tmp_path):
    digest,prior,report_path=_setup(tmp_path)
    state_path=tmp_path/"training_runs"/"current.json"
    state=json.loads(state_path.read_text())
    state["stage"]="SCORING"
    evaluate._atomic_json(state_path,state)
    with pytest.raises(ValueError,match="completed|COMPLETE"):
        manual_promotion.preview(tmp_path)
    state["stage"]="COMPLETE"
    state["evaluation_report"]=str(tmp_path/"another.json")
    evaluate._atomic_json(state_path,state)
    with pytest.raises(ValueError,match="path|report"):
        manual_promotion.preview(tmp_path)


def test_rollback_rejects_unknown_backup_and_missing_acknowledgement(tmp_path):
    digest,_,_= _setup(tmp_path)
    with pytest.raises(ValueError,match="acknowledg"):
        manual_promotion.rollback(tmp_path,expected_active_hash="f"*64,acknowledge=False)
    with pytest.raises(ValueError,match="backup|current|manual"):
        manual_promotion.rollback(tmp_path,expected_active_hash="f"*64,acknowledge=True)
